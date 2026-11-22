/* AI Intelligence Hub service worker.
 * - App shell + hashed assets: cache-first (they never change once built).
 * - API GETs: network-first; the last good response is kept so pages you already opened stay readable offline.
 *   Offline answers carry `X-Hub-Offline: 1` so the UI can say so.
 * - Never cached: non-GET requests, auth/devices endpoints, the live stream, other origins.
 * - On logout the page asks the worker to wipe cached API data (private data does not linger on the phone).
 */
const VERSION = "hub-v1";
const SHELL = `${VERSION}-shell`;
const API = `${VERSION}-api`;
const API_MAX_ENTRIES = 300;
const SHELL_URLS = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];
const NEVER_CACHE = ["/api/auth/", "/api/devices", "/api/stream"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "clear-api-cache") event.waitUntil(caches.delete(API));
});

async function trim(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - API_MAX_ENTRIES; i++) await cache.delete(keys[i]);
}

async function networkFirstApi(request) {
  const cache = await caches.open(API);
  try {
    const response = await fetch(request);
    if (response.ok && response.headers.get("content-type")?.includes("application/json")) {
      await cache.put(request, response.clone());
      trim(cache);
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (!cached) {
      return new Response(JSON.stringify({ detail: "offline and not saved on this device yet" }), {
        status: 503, headers: { "Content-Type": "application/json", "X-Hub-Offline": "1" },
      });
    }
    const headers = new Headers(cached.headers);
    headers.set("X-Hub-Offline", "1");
    return new Response(await cached.blob(), { status: cached.status, headers });
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

async function navigation(request) {
  try {
    const response = await fetch(request);
    const cache = await caches.open(SHELL);
    cache.put("/", response.clone());
    return response;
  } catch (err) {
    return (await caches.match("/")) || Response.error();
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (NEVER_CACHE.some((p) => url.pathname.startsWith(p))) return;
  if (req.mode === "navigate") return event.respondWith(navigation(req));
  if (url.pathname.startsWith("/api/")) return event.respondWith(networkFirstApi(req));
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/")) return event.respondWith(cacheFirst(req));
});
