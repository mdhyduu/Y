const CACHE_NAME = "pwa-cache-v2";
const STATIC_CACHE = "static-cache-v1";

// قائمة بجميع الملفات التي نريد تخزينها مؤقتاً
const urlsToCache = [
  "/",
  "/static/css/main.css",
  "/static/icons/icon-192x192.png",
  "/static/icons/s.png",
  "/static/manifest.json",
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css",
  "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css",
  "https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap",
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js",
  "https://code.jquery.com/jquery-3.6.0.min.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => {
        console.log('تم فتح التخزين المؤقت');
        return cache.addAll(urlsToCache);
      })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== STATIC_CACHE && cache !== CACHE_NAME) {
            console.log('جاري حذف التخزين المؤقت القديم:', cache);
            return caches.delete(cache);
          }
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // تجاهل طلبات غير GET وطلبات أخرى غير مهمة
  if (event.request.method !== 'GET') return;
  
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // إذا وجدنا الملف في التخزين المؤقت، نرجعه
        if (response) {
          return response;
        }

        // إذا لم نجده، نحمله من الشبكة
        return fetch(event.request)
          .then(response => {
            // تحقق من أن الرد صالح للتخزين
            if (!response || response.status !== 200 || response.type !== 'basic') {
              return response;
            }

            // استنساخ الرد لأن الرد يمكن استخدامه مرة واحدة فقط
            const responseToCache = response.clone();

            caches.open(CACHE_NAME)
              .then(cache => {
                cache.put(event.request, responseToCache);
              });

            return response;
          })
          .catch(() => {
            // إذا فشل التحميل، يمكننا إرجاع صفحة بديلة إذا كانت الصفحة الرئيسية
            if (event.request.url.indexOf('/static/') !== -1) {
              return caches.match('/');
            }
          });
      })
  );
});