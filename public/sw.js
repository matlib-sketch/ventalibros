// Cambia este nombre cada vez que subas un cambio en HTML/JS importante.
// Bumpea el numero al final.
const CACHE = 'libros-v5';

// Solo precacheamos cosas inmutables (icono, manifest).
// Los HTML los pedimos siempre a la red para no quedarnos con versiones viejas.
const STATIC_ASSETS = ['/icon.svg', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    Promise.all([
      caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
      ),
      self.clients.claim(),
    ])
  );
});

// Permite que la pagina mande "SKIP_WAITING" para activar la nueva version al toque
self.addEventListener('message', e => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', e => {
  const req = e.request;

  // Solo manejamos GET
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Misma origen unicamente
  if (url.origin !== self.location.origin) return;

  // Llamadas a la API: siempre a la red, sin pasar por la cache
  if (url.pathname.startsWith('/api/')) return;

  // Estrategia network-first para HTML / navegaciones.
  // Asi cualquier deploy llega instantaneo en cuanto el cel tiene red.
  const isHTML = req.mode === 'navigate'
              || req.destination === 'document'
              || (req.headers.get('accept') || '').includes('text/html');

  if (isHTML) {
    e.respondWith(
      fetch(req)
        .then(res => {
          // Guardamos copia fresca por si despues no hay red
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(req, clone));
          return res;
        })
        .catch(async () => {
          const cached = await caches.match(req);
          if (cached) return cached;
          const home = await caches.match('/');
          if (home) return home;
          return new Response('Sin conexion', { status: 503, statusText: 'Offline' });
        })
    );
    return;
  }

  // Para assets estaticos (imagenes, icono, manifest): cache-first con refresco en background
  e.respondWith(
    caches.match(req).then(cached => {
      const networkFetch = fetch(req).then(res => {
        if (res && res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(req, clone));
        }
        return res;
      }).catch(() => cached);
      return cached || networkFetch;
    })
  );
});
