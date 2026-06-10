import os
import json
import uuid
import base64
import hashlib
import urllib.request
import urllib.parse
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, Response

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
            cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS detail TEXT DEFAULT ''")
            cur.execute("ALTER TABLE books ALTER COLUMN price DROP NOT NULL")
            cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'novelas_judias'")
            cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS categories TEXT[] DEFAULT '{}'")
            # Descripcion larga (se muestra al abrir la foto) y fotos extra de la galeria.
            # 'photo' sigue siendo la foto de portada/presentacion; 'photos' son las
            # fotos adicionales. La galeria completa es [photo, *photos] (hasta 5).
            cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS long_description TEXT DEFAULT ''")
            cur.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS photos TEXT[] DEFAULT '{}'")

            # Registro de migraciones aplicadas (para no repetirlas en cada arranque).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INT PRIMARY KEY,
                    applied_at TEXT
                )
            """)

            # Migracion 1: renombramos la vieja categoria 'seculares' (que era el
            # default historico y agarraba todo lo que no estaba clasificado) a
            # 'novelas_judias'. Asi 'seculares' queda libre como categoria nueva
            # vacia para libros que realmente sean seculares.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 1")
            if not cur.fetchone():
                import datetime
                cur.execute("UPDATE books SET category = 'novelas_judias' WHERE category = 'seculares'")
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (1, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 2: recalculamos el precio de venta de todos los libros
            # que tienen precio internet, pasando del descuento viejo (10%) al
            # nuevo descuento (40%). price = original_price * 0.60.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 2")
            if not cur.fetchone():
                import datetime
                cur.execute(
                    "UPDATE books SET price = ROUND((original_price * 0.60)::numeric, 2) "
                    "WHERE original_price IS NOT NULL AND original_price > 0"
                )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (2, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 3: traspasamos todos los precios de USD a pesos chilenos
            # (CLP) multiplicando por 900. Aplica tanto al precio de venta como
            # al precio internet de referencia. Solo corre una vez.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 3")
            if not cur.fetchone():
                import datetime
                cur.execute(
                    "UPDATE books SET "
                    "price = ROUND((price * 900)::numeric, 0), "
                    "original_price = CASE WHEN original_price IS NOT NULL "
                    "THEN ROUND((original_price * 900)::numeric, 0) ELSE NULL END "
                    "WHERE price IS NOT NULL"
                )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (3, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 4: pasamos a multi-categoria. La columna 'categories' (TEXT[])
            # es la fuente de verdad. Copiamos el 'category' actual al array.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 4")
            if not cur.fetchone():
                import datetime
                cur.execute(
                    "UPDATE books SET categories = ARRAY[category] "
                    "WHERE category IS NOT NULL AND (categories IS NULL OR array_length(categories,1) IS NULL)"
                )
                cur.execute(
                    "UPDATE books SET categories = ARRAY['novelas_judias'] "
                    "WHERE categories IS NULL OR array_length(categories,1) IS NULL"
                )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (4, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 5: cambiamos el descuento de 40% a 60%.
            # price = original_price * 0.40
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 5")
            if not cur.fetchone():
                import datetime
                cur.execute(
                    "UPDATE books SET price = ROUND((original_price * 0.40)::numeric, 0) "
                    "WHERE original_price IS NOT NULL AND original_price > 0"
                )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (5, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )
        conn.commit()

# Categorias permitidas. Cualquier otra cosa se guarda como 'novelas_judias'.
VALID_CATEGORIES = {
    # libros
    'hebreo', 'ingles_espanol', 'novelas_judias', 'seculares', 'ninos', 'mujeres',
    # casa
    'cocina', 'decoracion', 'dormitorio', 'bano',
}

SECTION_CATEGORIES = {
    'libros': {'hebreo', 'ingles_espanol', 'novelas_judias', 'seculares', 'ninos', 'mujeres'},
    'casa':   {'cocina', 'decoracion', 'dormitorio', 'bano'},
}

def _section_of(cat):
    for section, cats in SECTION_CATEGORIES.items():
        if cat in cats:
            return section
    return 'libros'

def _clean_category(cat):
    if not cat:
        return 'novelas_judias'
    cat = str(cat).strip().lower()
    return cat if cat in VALID_CATEGORIES else 'novelas_judias'

def _clean_categories(cats):
    if not cats:
        return ['novelas_judias']
    if isinstance(cats, str):
        cats = [cats]
    result = [str(c).strip().lower() for c in cats if c]
    result = [c for c in result if c in VALID_CATEGORIES]
    return result if result else ['novelas_judias']

def read_books():
    if DATABASE_URL:
        import psycopg2.extras
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM books ORDER BY created_at DESC")
                rows = cur.fetchall()
        def _row_cats(r):
            raw = list(r.get('categories') or [])
            if not raw and r.get('category'):
                raw = [r['category']]
            return _clean_categories(raw)

        return [{
            'id':              r['id'],
            'title':           r['title'],
            'author':          r['author'] or '',
            'detail':          r['detail'] or '',
            'longDescription': r.get('long_description') or '',
            'originalPrice':   r['original_price'],
            'price':           r['price'],
            'photo':           r['photo'],
            'photos':          list(r.get('photos') or []),
            'sold':            r['sold'],
            'createdAt':       r['created_at'],
            'category':        _row_cats(r)[0],
            'categories':      _row_cats(r),
        } for r in rows]
    else:
        data_file = BASE_DIR / 'data' / 'books.json'
        data_file.parent.mkdir(parents=True, exist_ok=True)
        if not data_file.exists():
            return []
        try:
            books = json.loads(data_file.read_text(encoding='utf-8'))
            for b in books:
                cats = _clean_categories(b.get('categories') or ([b.get('category')] if b.get('category') else []))
                b['category'] = cats[0]
                b['categories'] = cats
                b['longDescription'] = b.get('longDescription') or ''
                b['photos'] = b.get('photos') or []
            return books
        except Exception:
            return []

def save_book_db(book):
    cats = _clean_categories(book.get('categories') or ([book.get('category')] if book.get('category') else []))
    photos = [p for p in (book.get('photos') or []) if p][:4]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO books (id, title, author, detail, long_description, original_price, price, photo, photos, sold, created_at, category, categories)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (book['id'], book['title'], book['author'], book.get('detail', ''),
                  book.get('longDescription', ''),
                  book['originalPrice'], book['price'],
                  book['photo'], photos, book['sold'], book['createdAt'],
                  cats[0], cats))
        conn.commit()

def delete_book_db(book_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM books WHERE id=%s", (book_id,))
        conn.commit()

def patch_book_db(book_id, sold=None, price=None, photo=None, title=None, author=None,
                  detail=None, category=None, long_description=None, photos=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if sold is not None:
                cur.execute("UPDATE books SET sold=%s WHERE id=%s", (sold, book_id))
            if price is not None:
                cur.execute("UPDATE books SET price=%s WHERE id=%s", (price, book_id))
            if photo is not None:
                cur.execute("UPDATE books SET photo=%s WHERE id=%s", (photo, book_id))
            if photos is not None:
                clean = [p for p in photos if isinstance(p, str) and p][:4]
                cur.execute("UPDATE books SET photos=%s WHERE id=%s", (clean, book_id))
            if title is not None:
                cur.execute("UPDATE books SET title=%s WHERE id=%s", (title, book_id))
            if author is not None:
                cur.execute("UPDATE books SET author=%s WHERE id=%s", (author, book_id))
            if detail is not None:
                cur.execute("UPDATE books SET detail=%s WHERE id=%s", (detail, book_id))
            if long_description is not None:
                cur.execute("UPDATE books SET long_description=%s WHERE id=%s", (long_description, book_id))
            if category is not None:
                cats = _clean_categories(category if isinstance(category, list) else [category])
                cur.execute("UPDATE books SET category=%s, categories=%s WHERE id=%s", (cats[0], cats, book_id))
        conn.commit()

def save_books_json(books):
    data_file = BASE_DIR / 'data' / 'books.json'
    data_file.parent.mkdir(parents=True, exist_ok=True)
    data_file.write_text(json.dumps(books, ensure_ascii=False, indent=2), encoding='utf-8')

if DATABASE_URL:
    try:
        init_db()
        print('Base de datos PostgreSQL conectada.')
    except Exception as e:
        print('Error conectando DB:', e)

# ---------- static ----------

# Archivos donde SIEMPRE queremos la version mas nueva. Si el navegador los cachea
# por horas, los celulares se quedan con versiones viejas y la app se rompe en silencio.
NO_CACHE_FILES = {'sw.js', 'index.html', 'admin.html'}

def _no_cache(resp):
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/')
def index():
    return _no_cache(send_from_directory('public', 'index.html'))

@app.route('/<path:filename>')
def static_files(filename):
    resp = send_from_directory('public', filename)
    if filename in NO_CACHE_FILES:
        return _no_cache(resp)
    return resp

# ---------- API ----------

@app.get('/api/verify')
def verify():
    err = check_auth()
    if err: return err
    return jsonify({'ok': True})

def _photo_version(photo):
    """Hash corto del data URL — cambia si la foto cambia, asi el navegador refresca."""
    if not photo:
        return None
    return hashlib.md5(photo[:300].encode('utf-8', errors='ignore')).hexdigest()[:8]

def _gallery_of(b):
    """Galeria completa de un item: portada + fotos extra, sin vacios."""
    gallery = [b.get('photo')] + list(b.get('photos') or [])
    return [p for p in gallery if p]

def _strip_photo(b):
    """Saca las fotos pesadas del item, deja solo flags livianos.

    El listado no manda el base64: cada foto se pide aparte por
    /api/books/<id>/photo/<idx>. Mandamos la cuenta y las versiones para
    saber cuantas fotos hay y poder invalidar la cache de cada una.
    """
    gallery = _gallery_of(b)
    out = {k: v for k, v in b.items() if k not in ('photo', 'photos')}
    out['hasPhoto'] = len(gallery) > 0
    out['photoCount'] = len(gallery)
    out['galleryVersions'] = [_photo_version(p) for p in gallery]
    out['photoVersion'] = out['galleryVersions'][0] if gallery else None
    return out

def _get_book_gallery(book_id):
    """Devuelve la lista de data URLs (portada + extras) de un item."""
    if DATABASE_URL:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT photo, photos FROM books WHERE id=%s", (book_id,))
                row = cur.fetchone()
        if not row:
            return []
        return _gallery_of({'photo': row[0], 'photos': row[1]})
    else:
        data_file = BASE_DIR / 'data' / 'books.json'
        if not data_file.exists():
            return []
        try:
            books = json.loads(data_file.read_text(encoding='utf-8'))
            book = next((b for b in books if b['id'] == book_id), None)
            return _gallery_of(book) if book else []
        except Exception:
            return []

@app.get('/api/books')
def get_books():
    # Devolvemos los libros SIN la foto en base64 (el listado pesa 100x menos).
    # Cada foto se pide aparte por /api/books/<id>/photo.
    books = read_books()
    return jsonify([_strip_photo(b) for b in books])

@app.get('/api/books/<book_id>/full')
def get_book_full(book_id):
    # Solo admin: devuelve el item completo CON las fotos en base64, para poder
    # editarlas (agregar/quitar) en el modal de edicion.
    err = check_auth()
    if err: return err
    book = next((b for b in read_books() if b['id'] == book_id), None)
    if not book:
        return jsonify({'error': 'No encontrado'}), 404
    return jsonify(book)

def _serve_gallery_image(book_id, idx):
    gallery = _get_book_gallery(book_id)
    if idx < 0 or idx >= len(gallery):
        return '', 404
    photo = gallery[idx]
    if not photo or not photo.startswith('data:'):
        return '', 404
    try:
        header, b64 = photo.split(',', 1)
        # header tipo "data:image/jpeg;base64"
        mime = 'image/jpeg'
        if ':' in header and ';' in header:
            mime = header.split(':', 1)[1].split(';', 1)[0] or 'image/jpeg'
        img_bytes = base64.b64decode(b64)
    except Exception:
        return '', 404

    resp = Response(img_bytes, mimetype=mime)
    # Cache largo: la URL incluye la version como querystring, asi cualquier cambio
    # de foto invalida la cache automaticamente.
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp

@app.get('/api/books/<book_id>/photo')
def get_book_photo(book_id):
    # Foto de portada (indice 0 de la galeria).
    return _serve_gallery_image(book_id, 0)

@app.get('/api/books/<book_id>/photo/<int:idx>')
def get_book_photo_idx(book_id, idx):
    return _serve_gallery_image(book_id, idx)

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
    if not title:
        return jsonify({'error': 'Faltan datos'}), 400
    price_raw = data.get('price')
    try:
        price = float(price_raw) if price_raw not in (None, '') else None
    except (ValueError, TypeError):
        price = None

    try:
        orig = float(data.get('originalPrice') or 0)
    except (ValueError, TypeError):
        orig = 0.0

    import datetime
    cats = _clean_categories(data.get('categories') or ([data.get('category')] if data.get('category') else []))
    photos = [p for p in (data.get('photos') or []) if p][:4]
    book = {
        'id':              str(uuid.uuid4()),
        'title':           title,
        'author':          (data.get('author') or '').strip(),
        'detail':          (data.get('detail') or '').strip(),
        'longDescription': (data.get('longDescription') or '').strip(),
        'originalPrice':   orig if orig > 0 else None,
        'price':           price,
        'photo':           data.get('photo') or None,
        'photos':          photos,
        'sold':            False,
        'createdAt':       datetime.datetime.utcnow().isoformat(),
        'category':        cats[0],
        'categories':      cats,
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
            photo=data.get('photo'),
            photos=data.get('photos'),
            title=data.get('title'),
            author=data.get('author'),
            detail=data.get('detail'),
            long_description=data.get('longDescription'),
            category=data.get('categories') or data.get('category'),
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
        if 'photos' in data:
            book['photos'] = [p for p in (data['photos'] or []) if p][:4]
        if 'title' in data:
            book['title'] = data['title']
        if 'author' in data:
            book['author'] = data['author']
        if 'detail' in data:
            book['detail'] = data['detail']
        if 'longDescription' in data:
            book['longDescription'] = data['longDescription']
        if 'category' in data:
            book['category'] = _clean_category(data['category'])
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
