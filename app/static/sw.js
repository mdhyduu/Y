// sw.js - النسخة المحسنة
const CACHE_NAME = 'dashboard-cache-v3'; // زيادة رقم الإصدار
const urlsToCache = [
  '/',
  '/static/css/main.css',
  '/static/js/main.js',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css',
  'https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap'
];

// تثبيت Service Worker
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function(cache) {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
      .catch(error => {
        console.error('Failed to cache:', error);
      })
  );
});

// تفعيل Service Worker
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(cacheNames) {
      return Promise.all(
        cacheNames.map(function(cacheName) {
          if (cacheName !== CACHE_NAME) {
            console.log('Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// Fetch events - نسخة مبسطة وأكثر أماناً
self.addEventListener('fetch', function(event) {
  // تجاهل طلبات غير GET
  if (event.request.method !== 'GET') {
    return;
  }
  
  event.respondWith(
    caches.match(event.request)
      .then(function(response) {
        // إذا وجدت في الكاش، أعرضها
        if (response) {
          return response;
        }
        
        // إذا لم توجد، أحمل من الشبكة
        return fetch(event.request).then(function(response) {
          // تحقق إذا كان الرد صالح للتخزين
          if(!response || response.status !== 200 || response.type !== 'basic') {
            return response;
          }
          
          // استنساخ الرد
          var responseToCache = response.clone();
          
          caches.open(CACHE_NAME)
            .then(function(cache) {
              cache.put(event.request, responseToCache);
            });
          
          return response;
        }).catch(function() {
          // في حالة الخطأ، يمكن إرجاع صفحة بديلة
          return caches.match('/offline.html');
        });
      })
  );
});