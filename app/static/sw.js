const CACHE_NAME = "pwa-cache-v11";  // زيادة رقم الإصدار لتحديث التخزين
const OFFLINE_DATA_CACHE = "offline-orders-data";
const urlsToCache = [
  "/",
  "/static/css/main.css",
  "/static/css/orders.css",
  "/static/icons/icon-192x192.png",
  "/static/icons/s.png",
  
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css",
  "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css",
  "https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap",
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js",
  "https://code.jquery.com/jquery-3.6.0.min.js"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return Promise.all(
          urlsToCache.map(url => {
            return fetch(url, { mode: 'no-cors' })
              .then(response => {
                if (response.status >= 400) {
                  throw new Error("Failed to fetch: " + url);
                }
                return cache.put(url, response);
              })
              .catch(error => {
                console.log("Could not cache: " + url, error);
              });
          })
        );
      })
      .then(() => {
        // إنشاء مخزن للبيانات غير المتصلة
        return caches.open(OFFLINE_DATA_CACHE);
      })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(cacheNames =>
      Promise.all(
        cacheNames.map(cache => {
          if (cache !== CACHE_NAME && cache !== OFFLINE_DATA_CACHE) {
            console.log("Deleting old cache: ", cache);
            return caches.delete(cache);
          }
        })
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // تجاهل طلبات غير GET
  if (event.request.method !== 'GET') return;
  
  // إذا كان الطلب لبيانات الطلبات المخزنة محلياً
  if (event.request.url.includes('/offline-data/')) {
    event.respondWith(
      caches.open(OFFLINE_DATA_CACHE).then(cache => {
        return cache.match(event.request).then(response => {
          return response || fetch(event.request);
        });
      })
    );
    return;
  }
  
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // إذا وجد المورد في الذاكرة المؤقتة
        if (response) {
          // تحديث الذاكرة المؤقتة في الخلفية
          fetchAndCache(event.request);
          return response;
        }
        
        // إذا لم يكن موجودًا في الذاكرة، نحمله من الشبكة
        return fetchAndCache(event.request);
      })
      .catch(() => {
        // إذا فشل كل شيء، نعيد الصفحة الرئيسية للتطبيق
        return caches.match('/');
      })
  );
});

// تخزين بيانات الطلبات للعمل بدون إنترنت
self.addEventListener("message", (event) => {
  if (event.data.type === "STORE_OFFLINE_ORDERS") {
    const { ordersData } = event.data;
    event.waitUntil(
      caches.open(OFFLINE_DATA_CACHE).then(cache => {
        const response = new Response(JSON.stringify(ordersData), {
          headers: { 'Content-Type': 'application/json' }
        });
        return cache.put('/offline-data/orders', response);
      })
    );
  }
  
  if (event.data.type === "GET_OFFLINE_ORDERS") {
    event.ports[0].postMessage({ 
      success: true, 
      data: event.data.ordersData 
    });
  }
});

function fetchAndCache(request) {
  return fetch(request)
    .then(response => {
      // تحقق من أن الرد صالح للتخزين
      if (!response || response.status !== 200 || response.type !== 'basic') {
        return response;
      }
      
      // استنساخ الرد لأن الجسم قابل للقراءة مرة واحدة فقط
      const responseToCache = response.clone();
      
      caches.open(CACHE_NAME)
        .then(cache => {
          cache.put(request, responseToCache);
        });
      
      return response;
    });
}