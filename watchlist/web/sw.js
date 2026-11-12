/* Watch Anything's service worker (only where the browser allows one: https, e.g. a Tailscale
   https name). It keeps the app's own files - its pages, script, style and icons - so the app
   opens quickly and can say when the PC isn't reachable. Nothing of yours is kept: never the
   app's answers (/api), posters or videos (what you download to the device is kept by the page
   itself, and plays from the kept player when the PC isn't there). */
const SHELL = 'watch-anything-shell-v2';
const FILES = ['/', '/index.html', '/app.js', '/phone.js', '/player.js', '/style.css', '/logo.png', '/favicon.png',
  '/manifest.webmanifest', '/icons/icon-180.png', '/icons/icon-192.png', '/icons/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || !FILES.includes(url.pathname)) return;
  // The PC's copy first (it may be newer); the kept one when the PC isn't there.
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); }
    return res;
  }).catch(() => caches.match(e.request).then((hit) => hit || caches.match('/'))));
});
