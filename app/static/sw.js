[file name]: sw.js
[file content begin]
const CACHE_NAME = "pwa-cache-v11";  // زيادة رقم الإصدار
const urlsToCache = [
  "/",
  "/static/css/main.css",
  "/static/css/orders.css",
  "/static/js/main.js",
  "/static/icons/icon-192x192.png",
  "/static/icons/icon-512x512.png",
  "/static/icons/s.png",
  "/orders/print_orders",  // إضافة صفحة طباعة الطلبات
  "/orders/download_orders_html",  // إضافة صفحة تحميل HTML
  
  // الموارد الخارجية
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css",
  "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css",
  "https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap",
  "https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js",
  "https://code.jquery.com/jquery-3.6.0.min.js",
  "https://kit.fontawesome.com/a076d05399.js"
];

// حدث التثبيت - تخزين الملفات في الذاكرة المؤقتة
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log("Opened cache");
        return cache.addAll(urlsToCache.map(url => new Request(url, { cache: 'reload' })));
      })
      .catch(error => {
        console.log("Cache installation failed:", error);
      })
  );
  self.skipWaiting();
});

// حدث التنشيط - تنظيف الذاكرة المؤقتة القديمة
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== CACHE_NAME) {
            console.log("Deleting old cache:", cache);
            return caches.delete(cache);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// حدث Fetch - إدارة الطلبات
self.addEventListener("fetch", (event) => {
  // تجاهل طلبات غير GET وطلبات الخادم الخارجي التي ليست في القائمة
  if (event.request.method !== 'GET') return;
  
  const url = new URL(event.request.url);
  
  // استراتيجية Cache First للموارد المحلية
  if (url.origin === location.origin || urlsToCache.includes(url.href)) {
    event.respondWith(
      caches.match(event.request)
        .then(response => {
          // إذا وجد المورد في الذاكرة المؤقتة
          if (response) {
            // تحديث الذاكرة في الخلفية
            fetchAndCache(event.request);
            return response;
          }
          
          // إذا لم يكن موجودًا، جلب من الشبكة
          return fetchAndCache(event.request);
        })
    );
  } else {
    // استراتيجية Network First للطلبات الخارجية
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // تخزين في الذاكرة إذا كانت الاستجابة ناجحة
          if (response.status === 200) {
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(event.request, responseClone);
            });
          }
          return response;
        })
        .catch(() => {
          // العودة إلى الذاكرة المؤقتة إذا فشل الاتصال
          return caches.match(event.request);
        })
    );
  }
});

// دالة مساعدة لجلب وتخزين الطلبات
function fetchAndCache(request) {
  return fetch(request)
    .then(response => {
      // التحقق من أن الرد صالح
      if (!response || response.status !== 200 || response.type !== 'basic') {
        return response;
      }
      
      // استنساخ الرد للتخزين
      const responseToCache = response.clone();
      
      caches.open(CACHE_NAME)
        .then(cache => {
          cache.put(request, responseToCache);
        });
      
      return response;
    })
    .catch(error => {
      // إذا فشل الجلب، البحث في الذاكرة المؤقتة
      return caches.match(request)
        .then(response => {
          return response || Promise.reject(error);
        });
    });
}

// دعم رسائل الخلفية للمزامنة
self.addEventListener('message', (event) => {
  if (event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// دعم ميزة الخلفية التزامنية
self.addEventListener('sync', (event) => {
  if (event.tag === 'background-sync') {
    event.waitUntil(doBackgroundSync());
  }
});

async function doBackgroundSync() {
  // هنا يمكنك إضافة منطق مزامنة البيانات عند توفر الاتصال
  console.log('Background sync started');
}
[file content end]