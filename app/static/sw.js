// اسم الكاش
const CACHE_NAME = "pwa-cache-v1";

// الملفات اللي تبي نخزنها (ممكن تعدلها حسب صفحاتك)
const urlsToCache = [
  "/",
  "/static/css/bootstrap.min.css",
  "/static/css/style.css",
  "/static/icons/s.png",
  "/static/manifest.json"
];

// تثبيت Service Worker + تخزين الملفات
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(urlsToCache);
    })
  );
  self.skipWaiting();
});

// تفعيل Service Worker + حذف الكاش القديم
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            return caches.delete(cache);
          }
        })
      )
    )
  );
  self.clients.claim();
});

// التعامل مع الطلبات (شبكة أولاً، ولو فشلت يجيب من الكاش)
self.addEventListener("fetch", (event) => {
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request)
    )
  );
});