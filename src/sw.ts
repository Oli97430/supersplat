import { version as appVersion } from '../package.json';

// export default null
declare let self: ServiceWorkerGlobalScope;

const cacheName = `superSplat-v${appVersion}`;

const cacheUrls = [
    './',
    './index.css',
    './index.html',
    './index.js',
    './index.js.map',
    './manifest.json',
    './static/icons/logo-192.png',
    './static/icons/logo-512.png',
    './static/images/screenshot-narrow.jpg',
    './static/images/screenshot-wide.jpg',
    './static/lib/lodepng/lodepng.js',
    './static/lib/lodepng/lodepng.wasm',
    './static/lib/webp/webp.mjs',
    './static/lib/webp/webp.wasm',
    './static/locales/de.json',
    './static/locales/en.json',
    './static/locales/fr.json',
    './static/locales/ja.json',
    './static/locales/ko.json',
    './static/locales/zh-CN.json'
];

self.addEventListener('install', (event) => {
    console.log(`installing v${appVersion}`);

    // Skip the waiting phase so a new SW immediately takes over.
    // Without this, the user has to close every tab + reopen to see the
    // updated assets — bumping version in package.json alone isn't enough.
    self.skipWaiting();

    // create cache for current version
    event.waitUntil(
        caches.open(cacheName)
        .then((cache) => {
            cache.addAll(cacheUrls);
        })
    );
});

self.addEventListener('activate', (event) => {
    console.log(`activating v${appVersion}`);

    event.waitUntil((async () => {
        // delete the old caches once this one is activated
        const names = await caches.keys();
        await Promise.all(
            names.filter(n => n !== cacheName).map(n => caches.delete(n))
        );
        // Claim all open clients so they start using the new SW now
        await self.clients.claim();
        // Tell every controlled tab a new version just took over.
        // The page-side listener (in index.html) renders an "update ready"
        // banner so users don't have to know about Ctrl+Shift+R.
        const clients = await self.clients.matchAll();
        for (const c of clients) {
            c.postMessage({ type: 'SW_UPDATED', version: appVersion });
        }
    })());
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // sw.js + index.html + manifest.json + the bare root: network-first so
    // a freshly built bundle is picked up without the user having to wipe
    // their browser cache. If the network is offline we still fall back to
    // the cached copy.
    const networkFirst =
        url.pathname.endsWith('/sw.js') ||
        url.pathname.endsWith('/index.html') ||
        url.pathname.endsWith('/manifest.json') ||
        url.pathname === '/' || url.pathname === '';
    if (networkFirst) {
        event.respondWith((async () => {
            try {
                const fresh = await fetch(event.request);
                // Refresh the cached copy in the background so offline still works.
                if (fresh.ok) {
                    const clone = fresh.clone();
                    caches.open(cacheName).then(c => c.put(event.request, clone)).catch(() => {});
                }
                return fresh;
            } catch {
                const cached = await caches.match(event.request);
                if (cached) return cached;
                throw new Error('offline and not cached');
            }
        })());
        return;
    }

    // Everything else: cache-first (snappy + works offline).
    event.respondWith(
        caches.match(event.request)
        .then(response => response ?? fetch(event.request))
    );
});
