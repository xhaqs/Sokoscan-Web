/**
 * SokoScan Service Worker — Security-Hardened v3.0
 */
const CACHE_VER = 'sokoscan-v3.0';
const APP_VER   = '3.0.0';

const ALLOWED_ORIGINS = [
  'https://query1.finance.yahoo.com',
  'https://query2.finance.yahoo.com',
  'https://api.allorigins.win',
  'https://corsproxy.io',
  'https://finnhub.io',
  'https://api.polygon.io',
  'https://api.unusualwhales.com',
  'https://api.benzinga.com',
  'https://fonts.googleapis.com',
  'https://fonts.gstatic.com',
];

const BLOCKED_PATTERNS = [
  /javascript:/i, /data:text\/html/i, /vbscript:/i,
  /<script/i, /\.\.[\/\\]/, /union\s+select/i,
  /\bexec\s*\(/i, /\bdrop\s+table/i,
];

const STATIC_ASSETS = [
  './', './index.html', './manifest.json',
  './icon-192.png', './icon-512.png'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_VER)
      .then(c => c.addAll(STATIC_ASSETS).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_VER).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);

  // Enforce HTTPS (allow localhost for dev)
  if (url.protocol !== 'https:' &&
      url.hostname !== 'localhost' &&
      url.hostname !== '127.0.0.1') {
    e.respondWith(new Response('HTTPS required', { status: 403 }));
    return;
  }

  // Block injection patterns in URL
  const decoded = decodeURIComponent(req.url);
  for (const p of BLOCKED_PATTERNS) {
    if (p.test(decoded)) {
      e.respondWith(new Response('Blocked', { status: 403 }));
      return;
    }
  }

  // Block unlisted external origins
  const isExternal = url.origin !== self.location.origin;
  if (isExternal && !ALLOWED_ORIGINS.some(o => req.url.startsWith(o))) {
    e.respondWith(new Response('Origin not permitted', { status: 403 }));
    return;
  }

  // API calls: network-only, no credentials cached
  const isApi = ALLOWED_ORIGINS.slice(0, 8).some(o => req.url.startsWith(o));
  if (isApi) {
    e.respondWith(
      fetch(req, { credentials: 'omit' }).catch(() =>
        new Response(
          JSON.stringify({ error: 'offline', offline: true }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        )
      )
    );
    return;
  }

  // Font files: cache-first
  if (req.url.includes('fonts.g')) {
    e.respondWith(
      caches.match(req).then(hit => hit || fetch(req).then(res => {
        caches.open(CACHE_VER).then(c => c.put(req, res.clone()));
        return res;
      }).catch(() => new Response('', { status: 503 })))
    );
    return;
  }

  // App shell: stale-while-revalidate
  e.respondWith(
    caches.match(req).then(cached => {
      const fresh = fetch(req)
        .then(res => {
          if (res.ok) caches.open(CACHE_VER).then(c => c.put(req, res.clone()));
          return res;
        })
        .catch(() => null);
      return cached || fresh ||
        new Response('SokoScan offline.', { status: 503 });
    })
  );
});

self.addEventListener('message', e => {
  if (e.data?.type === 'SKIP_WAITING') self.skipWaiting();
  if (e.data?.type === 'GET_VERSION')  e.ports[0]?.postMessage({ version: APP_VER });
  if (e.data?.type === 'CLEAR_CACHE') {
    caches.keys()
      .then(keys => Promise.all(keys.map(k => caches.delete(k))))
      .then(() => e.ports[0]?.postMessage({ ok: true }));
  }
});

// Push notifications
self.addEventListener('push', e => {
  if (!e.data) return;
  const d = e.data.json();
  e.waitUntil(self.registration.showNotification(d.title || 'SokoScan Alert', {
    body:    d.body || '',
    icon:    './icon-192.png',
    badge:   './icon-192.png',
    tag:     d.tag  || 'sokoscan',
    data:    { url: d.url || './' },
    vibrate: [200, 100, 200],
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window' }).then(list => {
      const u = e.notification.data?.url || './';
      for (const c of list) if ('focus' in c) return c.focus();
      if (clients.openWindow) return clients.openWindow(u);
    })
  );
});
