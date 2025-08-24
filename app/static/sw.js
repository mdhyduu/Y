// sw.js - النسخة المحسنة والمستقرة
const CACHE_NAME = 'dashboard-cache-v16';
const urlsToCache = [
  '/',
  '/static/css/main.css',
  '/static/js/main.js'
];

// استراتيجية التخزين: التخزين أولاً من الشبكة مع تحديث الكاش
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function(cache) {
        console.log('Service Worker: تحميل الموارد الأساسية في الكاش');
        return cache.addAll(urlsToCache);
      })
      .then(() => self.skipWaiting()) // التفعيل الفوري للخدمة
      .catch(error => {
        console.error('Service Worker: فشل في تحميل الموارد:', error);
      })
  );
});

// تنظيف الكاش القديم عند التفعيل
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(cacheNames) {
      return Promise.all(
        cacheNames.map(function(cacheName) {
          if (cacheName !== CACHE_NAME) {
            console.log('Service Worker: حذف الكاش القديم', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => {
      console.log('Service Worker: جاهز للتحكم في العملاء');
      return self.clients.claim();
    })
  );
});

// إستراتيجية التخزين: الشبكة أولاً مع الرجوع للكاش
self.addEventListener('fetch', function(event) {
  // تجاهل طلبات غير GET والمتعلقة بـ chrome-extension
  if (event.request.method !== 'GET' || event.request.url.includes('chrome-extension')) {
    return;
  }
  
  // معالجة طلبات CDN بشكل مختلف
  const isCDNRequest = event.request.url.includes('cdn.jsdelivr.net') || 
                       event.request.url.includes('cdnjs.cloudflare.com') ||
                       event.request.url.includes('fonts.googleapis.com');
  
  event.respondWith(
    fetch(event.request)
      .then(function(response) {
        // إذا كان الطلب ناجحاً، قم بتحديث الكاش
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME)
            .then(function(cache) {
              cache.put(event.request, responseToCache);
            });
        }
        return response;
      })
      .catch(function() {
        // إذا فشل الطلب، حاول استخدام الكاش
        return caches.match(event.request)
          .then(function(response) {
            // إذا وجد في الكاش، أعده
            if (response) {
              return response;
            }
            
            // إذا كان طلب CDN، حاول استخدام النسخة المخزنة مسبقاً
            if (isCDNRequest) {
              return caches.match('/offline.html');
            }
            
            // للطلبات الأخرى، أعد رسالة عدم اتصال
            return new Response('عذراً، أنت غير متصل بالإنترنت', {
              status: 503,
              statusText: 'Service Unavailable',
              headers: new Headers({
                'Content-Type': 'text/plain; charset=utf-8'
              })
            });
          });
      })
  );
});

// معالجة رسائل الخلفية
self.addEventListener('message', function(event) {
  if (event.data.action === 'skipWaiting') {
    self.skipWaiting();
  }
});

// معالجة المزامنة في الخلفية
self.addEventListener('sync', function(event) {
  if (event.tag === 'background-sync') {
    event.waitUntil(doBackgroundSync());
  }
});

async function doBackgroundSync() {
  // هنا يمكنك إضافة منطق المزامنة في الخلفية
  console.log('Service Worker: مزامنة في الخلفية');
}

// معالجة الإشعارات
self.addEventListener('push', function(event) {
  if (event.data) {
    const data = event.data.json();
    event.waitUntil(
      self.registration.showNotification(data.title, {
        body: data.body,
        icon: '/static/icons/s.png',
        badge: '/static/icons/s.png'
      })
    );
  }
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  event.waitUntil(
    clients.openWindow('/')
  );
});