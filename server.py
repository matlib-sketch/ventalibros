import os
import json
import uuid
import urllib.request
import urllib.parse
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='public')

BASE_DIR = Path(__file__).parent
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'libros123')
# Railway usa "postgres://" pero psycopg2 necesita "postgresql://"
_db_url = os.environ.get('DATABASE_URL', '')
DATABASE_URL = _db_url.replace('postgres://', 'postgresql://') if _db_url else None

def check_auth():
    pw = request.headers.get('X-Admin-Password', '')
    if pw != ADMIN_PASSWORD:
        return jsonify({'error': 'Sin permiso'}), 403
    return None

# ---------- base de datos ----------

def get_conn():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS books (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    author TEXT DEFAULT '',
                    original_price REAL,
                    price REAL NOT NULL,
                    photo TEXT,
                    sold BOOLEAN DEFAULT FALSE,
                    created_at TEXT
                )
            """)
        conn.commit()

def read_books():
    if DATABASE_URL:
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM books ORDER BY created_at DESC")
                rows = cur.fetchall()
        return [{
            'id':            r['id'],
            'title':         r['title'],
            'author':        r['author'] or '',
            'originalPrice': r['original_price'],
            'price':         r['price'],
            'photo':         r['photo'],
            'sold':          r['sold'],
            'createdAt':     r['created_at'],
        } for r in rows]
    else:
        # fallback local: archivo JSON
        data_file = BASE_DIR / 'data' / 'books.json'
        data_file.parent.mkdir(parents=True, exist_ok=True)
        if not data_file.exists():
            return []
        try:
            return json.loads(data_file.read_text(encoding='utf-8'))
        except Exception:
            return []

def save_book_db(book):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO books (id, title, author, original_price, price, photo, sold, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (book['id'], book['title'], book['author'],
                  book['originalPrice'], book['price'],
                  book['photo'], book['sold'], book['createdAt']))
        conn.commit()

def delete_book_db(book_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM books WHERE id=%s", (book_id,))
        conn.commit()

def patch_book_db(book_id, sold=None, price=None, photo=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if sold is not None:
                cur.execute("UPDATE books SET sold=%s WHERE id=%s", (sold, book_id))
            if price is not None:
                cur.execute("UPDATE books SET price=%s WHERE id=%s", (price, book_id))
            if photo is not None:
                cur.execute("UPDATE books SET photo=%s WHERE id=%s", (photo, book_id))
        conn.commit()

def save_books_json(books):
    data_file = BASE_DIR / 'data' / 'books.json'
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(books, ensure_ascii=False, indent=2), encoding='utf-8')

# Inicializar DB al arrancar
if DATABASE_URL:
    try:
        init_db()
        print('Base de datos PostgreSQL conectada.')
    except Exception as e:
        print('Error conectando DB:', e)

# ---------- static ----------

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('public', filename)

# ---------- API ----------

@app.get('/api/verify')
def verify():
    err = check_auth()
    if err: return err
    return jsonify({'ok': True})

@app.get('/api/books')
def get_books():
    return jsonify(read_books())

@app.get('/api/lookup')
def lookup():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'found': False})
    try:
        url = ('https://www.googleapis.com/books/v1/volumes?q='
               + urllib.parse.quote(q) + '&maxResults=8')
        req = urllib.request.Request(url, headers={'User-Agent': 'VentaLibros/1.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())

        items = data.get('items', [])
        if not items:
            return jsonify({'found': False})

        book_info = {}
        for item in items:
            info = item.get('volumeInfo', {})
            sale = item.get('saleInfo', {})
            if not book_info:
                book_info = {
                    'title':  info.get('title', q),
                    'author': ', '.join(info.get('authors', [])),
                }
            lp = sale.get('listPrice', {})
            if lp.get('amount'):
                return jsonify({
                    'found':    True,
                    'price':    lp['amount'],
                    'currency': lp.get('currencyCode', '$'),
                    'title':    info.get('title', q),
                    'author':   ', '.join(info.get('authors', [])),
                })

        return jsonify({'found': False, **book_info})
    except Exception as e:
        print('Lookup error:', e)
        return jsonify({'found': False})

@app.post('/api/books')
def add_book():
    err = check_auth()
    if err: return err

    data = request.get_json(force=True, silent=True) or {}
    title = (data.get('title') or '').strip()
    price = data.get('price')
    if not title or price is None:
        return jsonify({'error': 'Faltan datos'}), 400

    try:
        orig = float(data.get('originalPrice') or 0)
    except (ValueError, TypeError):
        orig = 0.0

    import datetime
    book = {
        'id':            str(uuid.uuid4()),
        'title':         title,
        'author':        (data.get('author') or '').strip(),
        'originalPrice': orig if orig > 0 else None,
        'price':         float(price),
        'photo':         data.get('photo') or None,
        'sold':          False,
        'createdAt':     datetime.datetime.utcnow().isoformat(),
    }

    if DATABASE_URL:
        save_book_db(book)
    else:
        books = read_books()
        books.insert(0, book)
        save_books_json(books)

    return jsonify({'success': True, 'book': book})

@app.delete('/api/books/<book_id>')
def delete_book(book_id):
    err = check_auth()
    if err: return err

    if DATABASE_URL:
        delete_book_db(book_id)
    else:
        books = [b for b in read_books() if b['id'] != book_id]
        save_books_json(books)

    return jsonify({'success': True})

@app.patch('/api/books/<book_id>')
def patch_book(book_id):
    err = check_auth()
    if err: return err

    data = request.get_json(force=True)
    if DATABASE_URL:
        patch_book_db(
            book_id,
            sold=data.get('sold'),
            price=float(data['price']) if 'price' in data else None,
            photo=data.get('photo')
        )
    else:
        books = read_books()
        book = next((b for b in books if b['id'] == book_id), None)
        if not book:
            return jsonify({'error': 'No encontrado'}), 404
        if 'price' in data:
            book['price'] = float(data['price'])
        if 'sold' in data:
            book['sold'] = bool(data['sold'])
        if 'photo' in data:
            book['photo'] = data['photo']
        save_books_json(books)

    return jsonify({'success': True})

# ---------- main ----------

if __name__ == '__main__':
    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = 'TU_IP'
    print('\nVentaLibros listo!')
    print(f'   Computadora: http://localhost:3000')
    print(f'   Celular (WiFi): http://{local_ip}:3000')
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)), debug=False)
