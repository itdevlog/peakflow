const CACHE = "peakflow-v1";
const SHELL = ["/", "/index.html", "/app.js", "/style.css", "/manifest.json", "/icon.svg"];
const BYPASS = ["/api/", "/healthz", "/metrics"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (BYPASS.some((p) => url.pathname.startsWith(p))) return;

  const networkFirst = (fallback) =>
    fetch(request).then((response) => {
      if (response.ok) {
        const copy = response.clone();
        event.waitUntil(caches.open(CACHE).then((cache) => cache.put(request, copy)));
      }
      return response;
    }).catch(() => caches.match(fallback));

  if (request.mode === "navigate") {
    event.respondWith(networkFirst("/index.html"));
    return;
  }
  event.respondWith(networkFirst(request));
});
