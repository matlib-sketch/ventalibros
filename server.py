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

def check_auth():
    pw = request.headers.get('X-Admin-Password', '')
    if pw != ADMIN_PASSWORD:
        return jsonify({'error': 'Sin permiso'}), 403
    return None

# En Railway usamos /data (volumen persistente); local usamos ./data
DATA_DIR  = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data'))
DATA_FILE = DATA_DIR / 'books.json'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------- helpers ----------

def read_books():
    if not DATA_FILE.exists():
        return []
    try:
        return json.loads(DATA_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []

def save_books(books):
    DATA_FILE.write_text(
        json.dumps(books, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

# ---------- static ----------

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('public', filename)

# ---------- API ----------

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

    book = {
        'id':            str(uuid.uuid4()),
        'title':         title,
        'author':        (data.get('author') or '').strip(),
        'originalPrice': orig if orig > 0 else None,
        'price':         float(price),
        'photo':         data.get('photo') or None,   # data URI base64
        'sold':          False,
        'createdAt':     __import__('datetime').datetime.utcnow().isoformat(),
    }

    books = read_books()
    books.insert(0, book)
    save_books(books)
    return jsonify({'success': True, 'book': book})

@app.delete('/api/books/<book_id>')
def delete_book(book_id):
    err = check_auth()
    if err: return err
    books = [b for b in read_books() if b['id'] != book_id]
    save_books(books)
    return jsonify({'success': True})

@app.patch('/api/books/<book_id>')
def patch_book(book_id):
    err = check_auth()
    if err: return err
    books = read_books()
    book  = next((b for b in books if b['id'] == book_id), None)
    if not book:
        return jsonify({'error': 'No encontrado'}), 404
    data = request.get_json(force=True)
    if 'price' in data:
        book['price'] = float(data['price'])
    if 'sold' in data:
        book['sold'] = bool(data['sold'])
    save_books(books)
    return jsonify({'success': True, 'book': book})

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
    print('   Para compartir publicamente sube a Railway.\n')
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)), debug=False)
