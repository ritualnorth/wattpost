/* WattPost service worker.
 *
 * Strategy:
 *   • The static shell (HTML, CSS, JS, uPlot, manifest, icons) is
 *     cached on install and served cache-first. This is what makes the
 *     "Add to Home Screen" experience instant on cold launch.
 *   • API requests (/api/*) and the SSE stream are network-only — we
 *     never want to cache live telemetry or hand the user stale data.
 *   • If the network fails on a navigation request, we fall back to
 *     the cached index.html so the app still boots and can show its
 *     own "Connecting…" / offline state via the dashboard.
 *
 * Bump CACHE_VERSION whenever the static asset cache busters in
 * index.html change so old shells are evicted on first visit after a
 * deploy.
 */
// Bump on every PR that touches index.html, app.js or styles.css —
// the inner cache-busters (?v=NN) don't help if the cached index.html
// itself is what's stale. Suffix corresponds to the current app.js
// version so future-me can see at a glance what's pinned.
const CACHE_VERSION = 'wattpost-v33-app122-css100';
const SHELL = [
  '/',
  '/web/styles.css',
  '/web/app.js',
  '/web/uPlot.min.css',
  '/web/uPlot.iife.min.js',
  '/manifest.webmanifest',
  '/web/icon.svg',
  '/web/icon-192.png',
  '/web/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_VERSION);
    // addAll is atomic — any 404 aborts the install and the SW is
    // rejected, which is what we want.
    await cache.addAll(SHELL);
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter(k => k !== CACHE_VERSION).map(k => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Bypass cross-origin (CDNs etc) — let the browser handle natively.
  if (url.origin !== self.location.origin) return;

  // API + SSE: never cached. Going offline against these is the daemon's
  // problem to surface; our cached UI handles the "Connecting…" state.
  if (url.pathname.startsWith('/api/')) return;

  // Static shell: cache-first.
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_VERSION);
    const cached = await cache.match(request);
    if (cached) return cached;
    try {
      const fresh = await fetch(request);
      // Only cache successful same-origin GETs.
      if (request.method === 'GET' && fresh.ok) {
        cache.put(request, fresh.clone()).catch(() => {});
      }
      return fresh;
    } catch (e) {
      // Offline. For navigation requests, fall back to the cached
      // index.html — the SPA renders its own offline notice via the
      // status pill.
      if (request.mode === 'navigate') {
        const index = await cache.match('/');
        if (index) return index;
      }
      throw e;
    }
  })());
});
