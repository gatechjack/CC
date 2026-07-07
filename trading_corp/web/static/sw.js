/* Trading Corp service worker.
 *
 * Caching strategy (deliberately conservative for a trading dashboard):
 *
 *   /static/*           → cache-first  (CSS, JS, fonts, icons rarely change)
 *   /offline.html       → cache-first  (must be available when offline)
 *   /webhook/*          → never cache  (external write surface; not user-facing)
 *   /api/* (future)     → never cache  (live data; clients handle freshness)
 *   /healthz            → never cache  (must hit live server to be useful)
 *   POST /any           → never cache  (no SW caching on writes, ever)
 *   /division/*, /, etc → network-first with offline fallback. Cache the last
 *                          successful HTML render so a momentary network blip
 *                          doesn't black-screen the app, but always prefer fresh.
 *
 * Cache invalidation: bump CACHE_VERSION below to force every client to drop
 * old caches on next activation. Increment whenever we ship a SW bug fix or
 * change a strategy.
 */

const CACHE_VERSION = "tc-v2";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const PAGE_CACHE   = `${CACHE_VERSION}-pages`;

// Pre-cached on install — must include the offline fallback page.
const PRECACHE = [
    "/offline.html",
    "/static/icon.svg",
    "/static/icons/icon-192.png",
    "/static/icons/apple-touch-icon-180.png",
    "/static/manifest.webmanifest",
];

// Routes that should NEVER be cached.
const NEVER_CACHE_PREFIXES = [
    "/webhook/",
    "/api/",
    "/healthz",
];

// ────────────────────────────────────────────────────────────────────────
// install — pre-cache critical assets so the offline page works on first
// load even before the user has visited anything.
// ────────────────────────────────────────────────────────────────────────
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(STATIC_CACHE)
            .then((cache) => cache.addAll(PRECACHE))
            .then(() => self.skipWaiting())  // activate immediately on update
    );
});

// ────────────────────────────────────────────────────────────────────────
// activate — clean up caches from previous versions so stale assets don't
// pile up on the user's device. Take control of any clients that were
// already open.
// ────────────────────────────────────────────────────────────────────────
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) => Promise.all(
            keys
                .filter((k) => !k.startsWith(CACHE_VERSION))
                .map((k) => caches.delete(k))
        )).then(() => self.clients.claim())
    );
});

// ────────────────────────────────────────────────────────────────────────
// fetch — route requests through the right strategy.
// ────────────────────────────────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
    const req = event.request;
    const url = new URL(req.url);

    // GET only.
    if (req.method !== "GET") return;

    // Same-origin only. Tailwind / fonts / HTMX from CDNs use the browser's
    // native HTTP cache, which is fine — we don't need to mediate those.
    if (url.origin !== self.location.origin) return;

    // Never cache API/webhook/health.
    for (const prefix of NEVER_CACHE_PREFIXES) {
        if (url.pathname.startsWith(prefix)) return;
    }

    // /sw.js must always be fresh (it's how SW updates ship) — never mediate it.
    if (url.pathname === "/sw.js") return;

    // Static assets: stale-while-revalidate — serve the cached copy instantly
    // for speed, but ALWAYS refetch in the background and update the cache, so
    // an updated CSS/JS lands within one navigation. (The old cache-first here
    // never revalidated, which pinned a stale /static/sfp_cockpit.css and left
    // the SFP cockpit's top nav unstyled until a hard refresh.)
    if (url.pathname.startsWith("/static/")) {
        event.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
        return;
    }

    // HTML pages: network-first with offline fallback.
    event.respondWith(networkFirst(req));
});

// ────────────────────────────────────────────────────────────────────────
// Strategies
// ────────────────────────────────────────────────────────────────────────

async function cacheFirst(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    if (cached) return cached;
    try {
        const fresh = await fetch(request);
        if (fresh.ok) cache.put(request, fresh.clone());
        return fresh;
    } catch (err) {
        // Asset can't be fetched and isn't cached. Surface a 504 so the
        // browser doesn't pretend everything's fine.
        return new Response("offline (uncached asset)", { status: 504 });
    }
}

// Stale-while-revalidate: return the cached copy immediately if present, and
// refresh the cache in the background from the network. Falls back to the
// network (then a 504) when nothing is cached yet.
async function staleWhileRevalidate(request, cacheName) {
    const cache = await caches.open(cacheName);
    const cached = await cache.match(request);
    const networked = fetch(request).then((fresh) => {
        if (fresh && fresh.ok) cache.put(request, fresh.clone());
        return fresh;
    }).catch(() => null);
    return cached || (await networked) || new Response("offline (uncached asset)", { status: 504 });
}

async function networkFirst(request) {
    const cache = await caches.open(PAGE_CACHE);
    try {
        const fresh = await fetch(request);
        // Only cache successful HTML responses. Skip 4xx/5xx — they can come
        // back wrong (e.g., HTMX fragment redirected to login) and we don't
        // want to store those.
        if (fresh.ok) {
            cache.put(request, fresh.clone());
        }
        return fresh;
    } catch (err) {
        // Network failed. Try the page cache, then the offline fallback.
        const cached = await cache.match(request);
        if (cached) return cached;
        const offline = await caches.match("/offline.html");
        return offline || new Response("offline", { status: 503 });
    }
}

// ────────────────────────────────────────────────────────────────────────
// push — Web Push handler (no-op until we wire push subscriptions).
// Stub now so we don't have to ship a SW update later just to enable push.
// ────────────────────────────────────────────────────────────────────────
self.addEventListener("push", (event) => {
    if (!event.data) return;
    let payload;
    try {
        payload = event.data.json();
    } catch {
        payload = { title: "Trading Corp", body: event.data.text() };
    }
    const options = {
        body: payload.body || "",
        icon: "/static/icons/icon-192.png",
        badge: "/static/icons/favicon-32.png",
        data: payload.data || {},
        tag: payload.tag,
        renotify: !!payload.tag,
    };
    event.waitUntil(self.registration.showNotification(payload.title || "Trading Corp", options));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = (event.notification.data && event.notification.data.url) || "/";
    event.waitUntil(
        clients.matchAll({ type: "window" }).then((wins) => {
            // Focus an existing window if there is one
            for (const w of wins) {
                if (w.url.endsWith(url) && "focus" in w) return w.focus();
            }
            return clients.openWindow(url);
        })
    );
});
