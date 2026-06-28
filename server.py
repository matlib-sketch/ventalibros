import os
import json
import uuid
import base64
import hashlib
import textwrap
import urllib.request
import urllib.parse
from io import BytesIO
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, Response

app = Flask(__name__, static_folder='public')

BASE_DIR = Path(__file__).parent
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'libros123')
# Railway usa "postgres://" pero psycopg2 necesita "postgresql://"
_db_url = os.environ.get('DATABASE_URL', '')
DATABASE_URL = _db_url.replace('postgres://', 'postgresql://') if _db_url else None

# Imagenes de carga inicial (seed). Viven como archivos en seed_assets/ y se
# guardan en la DB como data URLs base64, igual que las fotos que se suben desde
# el panel de administracion (ver _serve_gallery_image).
SEED_ASSETS_DIR = BASE_DIR / 'seed_assets'

def _seed_data_url(filename, mime='image/webp'):
    """Lee una imagen de seed_assets/ y la devuelve como data URL base64.
    Si el archivo no existe, devuelve None (el item queda sin esa foto)."""
    try:
        data = (SEED_ASSETS_DIR / filename).read_bytes()
    except OSError:
        return None
    return 'data:%s;base64,%s' % (mime, base64.b64encode(data).decode('ascii'))

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

            # Migracion 6: cargamos los primeros articulos de la seccion Casa
            # (electrodomesticos + herramientas), sin fotos. Las fotos se agregan
            # despues desde el panel de edicion. Idempotente por id (slug fijo).
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 6")
            if not cur.fetchone():
                import datetime
                now = datetime.datetime.utcnow().isoformat()
                for it in CASA_SEED:
                    cur.execute("""
                        INSERT INTO books
                            (id, title, author, detail, long_description, original_price,
                             price, photo, photos, sold, created_at, category, categories)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO NOTHING
                    """, (it['id'], it['title'], '', it['detail'], it['long'],
                          it.get('original_price'),
                          it['price'], None, [], False, now, it['category'], [it['category']]))
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (6, %s)",
                    (now,)
                )

            # Migracion 7: rellenamos el precio internet de referencia
            # (original_price) de los articulos de la seccion Casa cargados en la
            # migracion 6 sin ese dato. El valor es el punto medio del rango de
            # precio nuevo hoy en internet de cada equipo. Idempotente por id.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 7")
            if not cur.fetchone():
                import datetime
                for it in CASA_SEED:
                    if it.get('original_price') is not None:
                        cur.execute(
                            "UPDATE books SET original_price = %s WHERE id = %s",
                            (it['original_price'], it['id'])
                        )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (7, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 8: ajustamos el precio de venta de los articulos de la
            # seccion Casa para que sea un 40% de descuento sobre el precio
            # internet de referencia (price = original_price * 0.60). Tomamos el
            # valor ya redondeado de CASA_SEED. Idempotente por id.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 8")
            if not cur.fetchone():
                import datetime
                for it in CASA_SEED:
                    cur.execute(
                        "UPDATE books SET price = %s WHERE id = %s",
                        (it['price'], it['id'])
                    )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (8, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 9: agregamos el set de cama King (categoria dormitorio) a
            # la seccion Casa. Reinsertamos todo CASA_SEED con ON CONFLICT DO
            # NOTHING, asi solo entran los items que aun no existen (el set nuevo).
            # El INSERT ya trae price y original_price correctos. Idempotente por id.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 9")
            if not cur.fetchone():
                import datetime
                now = datetime.datetime.utcnow().isoformat()
                for it in CASA_SEED:
                    cur.execute("""
                        INSERT INTO books
                            (id, title, author, detail, long_description, original_price,
                             price, photo, photos, sold, created_at, category, categories)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (id) DO NOTHING
                    """, (it['id'], it['title'], '', it['detail'], it['long'],
                          it.get('original_price'),
                          it['price'], None, [], False, now, it['category'], [it['category']]))
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (9, %s)",
                    (now,)
                )

            # Migracion 10: agregamos las fotos del set de cama King. Las imagenes
            # viven en seed_assets/ y se guardan como data URLs base64 en la DB,
            # igual que las fotos subidas desde el panel. Portada = cama armada;
            # segunda foto = colchon. Solo setea si el item aun no tiene portada,
            # para no pisar fotos puestas a mano. Idempotente por version.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 10")
            if not cur.fetchone():
                import datetime
                cover = _seed_data_url('setcama-cama.webp')
                extra = [p for p in [_seed_data_url('setcama-colchon.webp')] if p]
                if cover:
                    cur.execute(
                        "UPDATE books SET photo=%s, photos=%s "
                        "WHERE id=%s AND (photo IS NULL OR photo='')",
                        (cover, extra, 'casa-set-cama-king-rosen-grafite')
                    )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (10, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 11: subimos el precio de referencia (original_price) del
            # set de cama King al valor nuevo de tienda de las 4 piezas sumadas
            # (~$1.500.000), en vez del tope de mercado usado ($550.000). Asi el
            # descuento mostrado (~71%) refleja el ahorro frente a comprarlas
            # nuevas. Idempotente por version.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 11")
            if not cur.fetchone():
                import datetime
                cur.execute(
                    "UPDATE books SET original_price=%s WHERE id=%s",
                    (1500000, 'casa-set-cama-king-rosen-grafite')
                )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (11, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 12: dejamos TODOS los articulos con un 75% de descuento
            # SIN tocar el precio de venta. Para eso fijamos el precio de
            # referencia (original_price, el tachado) en 4x el precio de venta
            # actual. Asi el descuento mostrado, 1 - price/original_price, da
            # exactamente 75% (porque price = original_price * 0.25). Pisa los
            # precios de referencia que ya existian para que todos queden en 75%,
            # incluido lo que no tenia referencia. Idempotente por version.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 12")
            if not cur.fetchone():
                import datetime
                cur.execute(
                    "UPDATE books SET original_price = ROUND((price * 4)::numeric, 0) "
                    "WHERE price IS NOT NULL AND price > 0"
                )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (12, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 13: la migracion 12 fijo la referencia en 4x el precio
            # (75% de descuento) para TODOS los articulos, pero ese cambio era
            # solo para la seccion Libros. Aca restauramos el precio de
            # referencia real (precio de internet) de los articulos de la seccion
            # Casa, tomando el valor canonico de CASA_SEED. Asi Casa vuelve a
            # mostrar su descuento real (40% / ~71%) y Libros queda en 75%.
            # Idempotente por version.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 13")
            if not cur.fetchone():
                import datetime
                for it in CASA_SEED:
                    op = it.get('original_price')
                    if op is not None:
                        cur.execute(
                            "UPDATE books SET original_price = %s WHERE id = %s",
                            (op, it['id'])
                        )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (13, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )

            # Migracion 14: bajamos el descuento de la seccion Libros de 75% a
            # 80%, SOLO en Libros. Mantenemos el precio de referencia
            # (original_price) y bajamos el precio de venta al 20% de la
            # referencia (price = original_price * 0.20 = 80% de descuento).
            # Ejemplo: un libro que se vendia en 25 (referencia 100, 75% off)
            # pasa a venderse en 20 (80% off). Excluimos la seccion Casa con la
            # misma logica que itemSection() del front: un item es de Casa si
            # alguna de sus categorias pertenece a Casa (usando 'categories' si
            # tiene, si no 'category'). Idempotente por version.
            cur.execute("SELECT 1 FROM schema_migrations WHERE version = 14")
            if not cur.fetchone():
                import datetime
                casa_cats = list(SECTION_CATEGORIES['casa'])
                cur.execute(
                    "UPDATE books SET price = ROUND((original_price * 0.20)::numeric, 0) "
                    "WHERE original_price IS NOT NULL AND original_price > 0 "
                    "AND NOT ("
                    "  (CASE WHEN array_length(categories, 1) >= 1 THEN categories "
                    "        ELSE ARRAY[category] END) && %s::text[]"
                    ")",
                    (casa_cats,)
                )
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (14, %s)",
                    (datetime.datetime.utcnow().isoformat(),)
                )
        conn.commit()

# Categorias permitidas. Cualquier otra cosa se guarda como 'novelas_judias'.
VALID_CATEGORIES = {
    # libros
    'hebreo', 'ingles_espanol', 'novelas_judias', 'seculares', 'ninos', 'mujeres',
    # casa
    'cocina', 'decoracion', 'dormitorio', 'bano', 'herramientas',
}

SECTION_CATEGORIES = {
    'libros': {'hebreo', 'ingles_espanol', 'novelas_judias', 'seculares', 'ninos', 'mujeres'},
    'casa':   {'cocina', 'decoracion', 'dormitorio', 'bano', 'herramientas'},
}

# Carga inicial de la sección Casa (migración 6). Cada item entra sin fotos:
# las fotos se agregan después desde el panel de edición. El id es un slug fijo
# para que la migración sea idempotente (ON CONFLICT DO NOTHING).
CASA_SEED = [
    {
        'id': 'casa-refrigerador-samsung-617',
        'title': 'Refrigerador Samsung Side by Side 617L',
        'detail': '617 L · Side by Side · No Frost · Inverter · color Silver. Como nuevo (sept. 2020).',
        'price': 567000,
        # Precio nuevo hoy en internet: $790.000 – $1.100.000 (punto medio).
        'original_price': 945000,
        'category': 'cocina',
        'long': textwrap.dedent('''\
            Refrigerador Samsung Side by Side 617 Litros — RS65R5411M9/ZS (SpaceMax)

            Vendo refrigerador Samsung Side by Side de 617 litros, color Silver. Comprado en septiembre de 2020, en excelente estado y funcionando perfecto.

            ✅ Dispensador de agua y hielo funcionando perfecto y limpios
            ✅ Tecnología SpaceMax (más capacidad interior)
            ✅ Sistema No Frost (no se forma escarcha)
            ✅ Compresor Digital Inverter (bajo consumo y silencioso)
            ✅ 617 litros totales — 415 L refrigerador / 202 L congelador
            ✅ Enfría parejo, sin ruidos, muy bien mantenido y limpio

            Ideal para familias o quienes necesitan harta capacidad. Funciona como nuevo, solo lo vendo por cambio de casa.

            💲 Precio: $567.000 (conversable)
            📍 Retiro en Santiago. El comprador coordina el traslado.
            📸 Fotos reales del equipo funcionando.'''),
    },
    {
        'id': 'casa-microondas-somela-reflection-3000',
        'title': 'Microondas Grill Somela Reflection 3000 DGM',
        'detail': '30 L · grill 2 en 1 · puerta espejada. Impecable (jul. 2021).',
        'price': 68990,
        # Precio nuevo hoy en internet: $99.990 – $129.990 (punto medio).
        'original_price': 114990,
        'category': 'cocina',
        'long': textwrap.dedent('''\
            Microondas Grill Somela Reflection 3000 DGM — 30 Litros

            Vendo microondas grill Somela Reflection 3000 DGM, 30 litros, puerta espejada. Comprado en julio de 2021, funcionando perfecto y muy bien cuidado.

            ✅ Funciona perfecto (microondas 1400W + grill 1100W)
            ✅ 2 en 1: microondas + grill para dorar y gratinar
            ✅ Incluye rejilla metálica del grill y bandeja de vidrio giratoria
            ✅ Limpio por dentro y por fuera, sin manchas ni rayas
            ✅ Panel digital, encendido automático y bloqueo de seguridad para niños
            ✅ 30 litros — espacio para fuentes grandes

            Anda como nuevo (nuevo cuesta sobre $100.000). Solo lo vendo por cambio de casa.

            💲 Precio: $68.990 (conversable)
            📍 Retiro en Santiago.
            📸 Fotos reales del equipo.'''),
    },
    {
        'id': 'casa-microondas-samsung-me83',
        'title': 'Microondas Samsung ME83 — 23 L',
        'detail': '23 L · 700W · puerta espejada. Compacto, ideal depto. 3 años de uso.',
        'price': 43500,
        # Precio nuevo hoy en internet: $60.000 – $85.000 (punto medio).
        'original_price': 72500,
        'category': 'cocina',
        'long': textwrap.dedent('''\
            Microondas Samsung ME83 — 23 Litros

            Vendo microondas Samsung ME83, 23 litros, 700W, puerta espejada. Tiene 3 años de uso, funcionando perfecto y muy bien cuidado.

            ✅ Funciona perfecto, calienta parejo
            ✅ Interior de esmalte cerámico (fácil de limpiar, antibacterial)
            ✅ Incluye bandeja de vidrio giratoria
            ✅ Limpio por dentro y por fuera
            ✅ Diseño espejado, compacto — ideal para cocinas chicas, departamentos o pieza
            ✅ 23 litros, 6 niveles de potencia

            Anda como nuevo. Solo lo vendo por cambio de casa.

            💲 Precio: $43.500 (conversable)
            📍 Retiro en Santiago.
            📸 Fotos reales del equipo.'''),
    },
    {
        'id': 'casa-lavavajillas-fensa-9430',
        'title': 'Lavavajillas Fensa Computer 9430 Inox',
        'detail': '14 cubiertos · acero inox · 6 programas. Impecable (jul. 2021).',
        'price': 194990,
        # Precio nuevo hoy en internet: $239.990 – $409.990 (punto medio).
        'original_price': 324990,
        'category': 'cocina',
        'long': textwrap.dedent('''\
            Lavavajillas Fensa Computer 9430 Inox — 14 Cubiertos

            Vendo lavavajillas Fensa Computer 9430, frontal en acero inoxidable, 14 cubiertos. Instalado en julio de 2021, impecable y funcionando perfecto.

            ✅ Funciona perfecto: lava y seca sin problemas, sin fugas
            ✅ 14 cubiertos — capacidad grande para toda la familia
            ✅ 6 programas de lavado + panel digital
            ✅ Tercera bandeja exclusiva para cubiertos (más espacio para vajilla)
            ✅ Frontal e interior en acero inoxidable
            ✅ Impecable por dentro y por fuera, con todos los canastos y bandejas

            Anda como nuevo (nuevo cuesta sobre $280.000). Lo vendo por cambio de casa.

            💲 Precio: $194.990 (conversable)
            📍 Retiro en Santiago. Lo entrego desinstalado y listo para llevar.
            📸 Fotos reales del equipo. Te muestro un video funcionando.'''),
    },
    {
        'id': 'casa-hidrolavadora-bauker-fast-plus',
        'title': 'Hidrolavadora Bauker Fast Plus 1650 PSI',
        'detail': 'Alta presión 1650 PSI · 1400W · con todos los accesorios. Poco uso (1 año).',
        'price': 36000,
        # Precio nuevo hoy en internet: $45.000 – $75.000 (punto medio).
        'original_price': 60000,
        'category': 'herramientas',
        'long': textwrap.dedent('''\
            Hidrolavadora Bauker Fast Plus — Alta Presión 1650 PSI

            Vendo hidrolavadora eléctrica Bauker Fast Plus, alta presión (1650 PSI / ~113 bar), 1400W. Tiene solo 1 año de uso, funciona perfecto y viene con todos sus accesorios.

            ✅ Funciona perfecto, toma presión sin problemas
            ✅ Incluye todo: pistola, lanza, manguera de 4 m, boquillas y conector de agua
            ✅ Ideal para lavar auto, patio, rejas, terrazas y muros
            ✅ Compacta y liviana, fácil de guardar
            ✅ Muy poco uso, en excelente estado

            Como nueva cuesta sobre $50.000. La vendo por cambio de casa.

            💲 Precio: $36.000 (conversable)
            📍 Retiro en Santiago.
            📸 Fotos reales del equipo.'''),
    },
    {
        'id': 'casa-generador-powerpro-xt35ig',
        'title': 'Generador Inverter PowerPro XT35iG 3.5 KVA',
        'detail': 'Inverter 3.5 KVA gasolina · onda pura · solo 4 hrs de uso. Casi nuevo.',
        'price': 279000,
        # Precio nuevo hoy en internet: $436.000 – $495.490 (punto medio).
        'original_price': 465000,
        'category': 'herramientas',
        'long': textwrap.dedent('''\
            Generador Inverter PowerPro XT35iG — 3.5 KVA Gasolina

            Vendo generador eléctrico inverter PowerPro XT35iG, a gasolina, 3.5 KVA (3.200W continuos / 3.500W peak). Tiene 4 años pero solo 4 horas de uso reales — está prácticamente nuevo y parte muy fácil.

            ✅ Solo 4 horas de uso (lo muestra la pantalla digital de horas)
            ✅ Parte a la primera, sin problemas
            ✅ Tecnología inverter / onda pura — seguro para notebook, TV, refrigerador y electrónica sensible
            ✅ Motor 4 tiempos 212cc, estanque 7 L, autonomía 3–4 hrs
            ✅ Salidas 220V + puertos USB, partida manual
            ✅ Ideal como respaldo para casa, parcela, taller o eventos
            ✅ Certificación SEC, conservado impecable

            Nuevo cuesta sobre $436.000. Lo vendo por cambio de casa.

            💲 Precio: $279.000 (conversable)
            📍 Retiro en Santiago. Te lo muestro funcionando antes de comprar.
            📸 Fotos reales del equipo.'''),
    },
    {
        'id': 'casa-set-cama-king-rosen-grafite',
        'title': 'Set de cama King — Colchón Rosen Grafite + base + respaldo + 2 veladores',
        'detail': 'Colchón Rosen Grafite King 180×200 + base dividida + respaldo de madera + par de veladores. Muy buen estado. Se vende como set.',
        'price': 440000,
        # Precio de referencia tachado = valor nuevo de tienda de las 4 piezas
        # sumadas (~$1.500.000). Frente a eso, el set en $440.000 queda con ~71%
        # de descuento. (El mercado usado por pieza va en la descripcion.)
        'original_price': 1500000,
        'category': 'dormitorio',
        'long': textwrap.dedent('''\
            Set de cama King — Colchón Rosen Grafite + base dividida + respaldo + par de veladores

            Vendo set de cama King completo, listo para armar y dormir. Todo en muy buen estado y bien cuidado. Se vende como conjunto.

            Incluye:

            ✅ Colchón Rosen Grafite — King (180×200)
            Modelo Grafite de Rosen, marca reconocida por su durabilidad. Muy buen estado, sin manchas ni hundimientos, firmeza intacta.
            Mercado usado: $150.000–$220.000 · Nuestro precio: $180.000

            ✅ Base dividida Flex — King (2 cuerpos de 1 plaza)
            Base box dividida en 2 cuerpos de una plaza, práctica para subir por escaleras. Tapiz café, estructura firme, patas de madera.
            Mercado usado: $80.000–$150.000 · Nuestro precio: $120.000

            ✅ Respaldo King de madera
            Cabecera King en madera clara, líneas simples. Combina con cualquier dormitorio. Sólido.
            Mercado usado: $55.000–$100.000 · Nuestro precio: $70.000

            ✅ Par de veladores de madera
            Dos veladores haciendo juego, madera clara con cajones. Se venden como par.
            Mercado usado: $50.000–$80.000 · Nuestro precio: $70.000

            💡 Compradas nuevas en tienda, las 4 piezas suman alrededor de $1.500.000. El set completo, en muy buen estado, se va en $440.000 — cerca de un 71% menos.

            💲 Precio del set completo: $440.000 (conversable)
            📍 Retiro en Santiago. El comprador coordina el traslado.
            📸 Fotos reales del set.'''),
    },
]

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

# Centinela para distinguir "el campo no se envio" de "se envio vacio/None".
_UNSET = object()

def _coerce_orig(v):
    """Normaliza el precio de referencia: numero positivo, o None para quitarlo."""
    try:
        v = float(v) if v not in (None, '') else None
    except (ValueError, TypeError):
        return None
    return v if (v is not None and v > 0) else None

def patch_book_db(book_id, sold=None, price=None, photo=None, title=None, author=None,
                  detail=None, category=None, long_description=None, photos=None,
                  original_price=_UNSET):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if sold is not None:
                cur.execute("UPDATE books SET sold=%s WHERE id=%s", (sold, book_id))
            if price is not None:
                cur.execute("UPDATE books SET price=%s WHERE id=%s", (price, book_id))
            if original_price is not _UNSET:
                cur.execute("UPDATE books SET original_price=%s WHERE id=%s", (original_price, book_id))
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
            original_price=_coerce_orig(data.get('originalPrice')) if 'originalPrice' in data else _UNSET,
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
        if 'originalPrice' in data:
            book['originalPrice'] = _coerce_orig(data['originalPrice'])
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

# ---------- catalogo PDF ----------

# Etiquetas lindas para mostrar en el PDF (las mismas que usa la web).
CATEGORY_LABELS = {
    'hebreo': 'Hebreo', 'ingles_espanol': 'Torá Ing/Esp',
    'novelas_judias': 'Novelas Judías', 'seculares': 'Seculares',
    'ninos': 'Niños', 'mujeres': 'Mujeres',
    'cocina': 'Cocina', 'decoracion': 'Decoración',
    'dormitorio': 'Dormitorio', 'bano': 'Baño', 'herramientas': 'Herramientas',
}

# Orden en que aparecen las categorias dentro de cada seccion en el PDF.
SECTION_ORDER = {
    'libros': ['hebreo', 'ingles_espanol', 'novelas_judias', 'ninos', 'mujeres', 'seculares'],
    'casa':   ['cocina', 'decoracion', 'dormitorio', 'bano', 'herramientas'],
}

SECTION_TITLES = {'libros': 'Libros', 'casa': 'Casa'}

# Emojis -> texto. Las fuentes estandar del PDF (Helvetica) no dibujan emojis,
# asi que los reemplazamos por algo legible antes de imprimir.
_EMOJI_MAP = {
    '✅': '•', '☑️': '•', '✔️': '•', '❌': '-',
    '💲': '', '📍': '', '📸': '', '📷': '', '🎥': '', '🎬': '',
    '✡️': '', '🏠': '', '📚': '', '📖': '', '🛒': '', '🔥': '', '⭐': '',
}

def _pdf_text(s):
    """Deja el texto seguro para imprimir en el PDF: cambia emojis por texto y
    bota cualquier caracter que la fuente no sepa dibujar (manteniendo tildes y ñ)."""
    if not s:
        return ''
    for k, v in _EMOJI_MAP.items():
        s = s.replace(k, v)
    # cp1252 = la codificacion que usa Helvetica en reportlab (incluye tildes, ñ, •).
    return s.encode('cp1252', 'ignore').decode('cp1252')

def _fmt_clp(v):
    """Formatea un numero como precio en pesos chilenos: 567000 -> $567.000"""
    if v is None:
        return ''
    try:
        return '$' + format(int(round(float(v))), ',').replace(',', '.')
    except (ValueError, TypeError):
        return ''

def _section_items(section):
    """Items de una seccion, no vendidos, agrupados por categoria en orden."""
    order = SECTION_ORDER.get(section, [])
    order_set = set(order)
    groups = {cat: [] for cat in order}
    for b in read_books():
        if b.get('sold'):
            continue
        cats = b.get('categories') or [b.get('category')]
        # El item va bajo la primera categoria suya que pertenezca a la seccion.
        target = next((c for c in cats if c in order_set), None)
        if target is None:
            continue
        groups[target].append(b)
    return [(cat, groups[cat]) for cat in order if groups[cat]]

def build_catalog_pdf(section):
    """Arma el catalogo PDF de una seccion (con fotos) y lo devuelve en bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle, KeepTogether,
    )
    from xml.sax.saxutils import escape

    BROWN = colors.HexColor('#2c1810')
    GOLD = colors.HexColor('#8a6d3b')
    GREEN = colors.HexColor('#2e7d32')
    GREY = colors.HexColor('#777777')

    styles = getSampleStyleSheet()
    h_title = ParagraphStyle('CatTitle', parent=styles['Title'],
                             textColor=BROWN, fontSize=24, spaceAfter=2)
    h_sub = ParagraphStyle('CatSub', parent=styles['Normal'],
                           textColor=GREY, fontSize=10, alignment=1, spaceAfter=6)
    h_cat = ParagraphStyle('CatCat', parent=styles['Heading2'],
                           textColor=GOLD, fontSize=15, spaceBefore=10, spaceAfter=4)
    s_name = ParagraphStyle('ItemName', parent=styles['Normal'],
                            fontName='Helvetica-Bold', fontSize=12,
                            textColor=BROWN, spaceAfter=3, leading=15)
    s_detail = ParagraphStyle('ItemDetail', parent=styles['Normal'],
                              fontSize=9.5, leading=13, spaceAfter=3)
    s_long = ParagraphStyle('ItemLong', parent=styles['Normal'],
                            fontSize=8.5, leading=12, textColor=colors.HexColor('#444444'))
    s_price = ParagraphStyle('ItemPrice', parent=styles['Normal'],
                             fontName='Helvetica-Bold', fontSize=14,
                             textColor=GREEN, spaceBefore=2)
    s_orig = ParagraphStyle('ItemOrig', parent=styles['Normal'],
                            fontSize=8.5, textColor=GREY)

    def P(text, style):
        return Paragraph(escape(_pdf_text(text)).replace('\n', '<br/>'), style)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=1.6 * cm, rightMargin=1.6 * cm,
        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
        title='Catálogo ' + SECTION_TITLES.get(section, section.title()),
    )

    story = []
    story.append(P('Catálogo · ' + SECTION_TITLES.get(section, section.title()), h_title))
    story.append(Paragraph('Tienda — Libros y cosas del hogar', h_sub))
    story.append(Spacer(1, 0.3 * cm))

    img_w = 4.6 * cm
    text_w = doc.width - img_w - 0.4 * cm
    n_items = 0

    for cat, items in _section_items(section):
        story.append(P(CATEGORY_LABELS.get(cat, cat.title()), h_cat))
        for b in items:
            n_items += 1
            # ---- columna de texto ----
            cell = [P(b.get('title') or '(sin nombre)', s_name)]
            if b.get('detail'):
                cell.append(P(b['detail'], s_detail))
            if b.get('longDescription'):
                cell.append(P(b['longDescription'], s_long))
            cell.append(P(_fmt_clp(b.get('price')), s_price))
            orig = b.get('originalPrice')
            if orig and b.get('price') and orig > b['price']:
                desc = round((1 - b['price'] / orig) * 100)
                cell.append(P('Precio internet ' + _fmt_clp(orig)
                              + '  ·  ' + str(desc) + '% off', s_orig))

            # ---- columna de imagen ----
            img_flowable = ''
            gallery = _gallery_of(b)
            if gallery:
                try:
                    header, b64 = gallery[0].split(',', 1)
                    raw = base64.b64decode(b64)
                    reader = ImageReader(BytesIO(raw))
                    iw, ih = reader.getSize()
                    h = img_w * ih / iw if iw else img_w
                    h = min(h, 5.0 * cm)
                    w = h * iw / ih if ih else img_w
                    w = min(w, img_w)
                    img_flowable = Image(BytesIO(raw), width=w, height=h)
                except Exception:
                    img_flowable = ''

            row = Table([[img_flowable, cell]], colWidths=[img_w, text_w])
            row.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (0, 0), 0),
                ('LEFTPADDING', (1, 0), (1, 0), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LINEBELOW', (0, 0), (-1, -1), 0.5, colors.HexColor('#e0d5c5')),
            ]))
            story.append(KeepTogether(row))

    if n_items == 0:
        story.append(P('Todavía no hay artículos en esta sección.', s_detail))

    doc.build(story)
    return buf.getvalue()

@app.get('/api/catalog/<section>.pdf')
def catalog_pdf(section):
    section = (section or '').lower()
    if section not in SECTION_ORDER:
        return jsonify({'error': 'Sección no válida'}), 404
    try:
        pdf = build_catalog_pdf(section)
    except ImportError:
        return jsonify({'error': 'Falta la librería reportlab en el servidor'}), 500
    except Exception as e:
        print('Error generando catálogo PDF:', e)
        return jsonify({'error': 'No se pudo generar el catálogo'}), 500

    filename = 'catalogo-' + section + '.pdf'
    resp = Response(pdf, mimetype='application/pdf')
    resp.headers['Content-Disposition'] = 'attachment; filename="' + filename + '"'
    return _no_cache(resp)

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
