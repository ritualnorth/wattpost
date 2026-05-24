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
const CACHE_VERSION = 'wattpost-v131-app216-css138';
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
    // Evict every cache that isn't the current version. Without this
    // the old shell hangs around indefinitely, costing disk + giving
    // ammo to "cache poisoning" debugging confusion (stale content
    // served because the OLD SW was still active on a long-running
    // tab). Belt-and-braces with skipWaiting + claim above.
    const live = await caches.keys();
    await Promise.all(live.map((k) => k === CACHE_VERSION ? null : caches.delete(k)));
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

  // Navigation requests: network-first. A cache-first navigation hands
  // the browser the *previous* index.html, which references the
  // *previous* app.js?v=N cache-buster, which is also still cached —
  // so a stale shell self-perpetuates and any client-side fix (like
  // the broker-view SSE skip) never reaches the device. Going to
  // network first means an online client always gets the latest shell;
  // the cached copy survives only as the offline fallback.
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_VERSION);
      try {
        const fresh = await fetch(request);
        if (fresh.ok) cache.put(request, fresh.clone()).catch(() => {});
        return fresh;
      } catch (e) {
        const index = await cache.match('/');
        if (index) return index;
        throw e;
      }
    })());
    return;
  }

  // Static sub-resources (css, js, icons): cache-first is still right —
  // they're version-keyed by the ?v= cache-buster in the shell, so a
  // fresh shell brings a fresh app.js URL that misses the old cache.
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_VERSION);
    const cached = await cache.match(request);
    if (cached) return cached;
    try {
      const fresh = await fetch(request);
      if (request.method === 'GET' && fresh.ok) {
        cache.put(request, fresh.clone()).catch(() => {});
      }
      return fresh;
    } catch (e) {
      throw e;
    }
  })());
});
