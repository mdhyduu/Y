// sw.js - النسخة المحسنة باستراتيجية الكاش أولاً للموارد الثابتة
const CACHE_NAME = 'dashboard-cache-v5';
const STATIC_CACHE = 'static-resources-v2';
const urlsToCache = [
  '/',
  '/static/css/main.css',
  '/static/js/main.js',
  '/static/icons/s.png',
  '/offline.html'
];

// استراتيجية التخزين: التخزين أولاً من الشبكة مع تحديث الكاش
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(function(cache) {
        console.log('Service Worker: تحميل الموارد الثابتة في الكاش');
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
          // حذف الكاش القديم باستثناء الكاش الحالي والكاش الثابت
          if (cacheName !== CACHE_NAME && cacheName !== STATIC_CACHE) {
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

// إستراتيجية التخزين: الكاش أولاً للموارد الثابتة، الشبكة أولاً للطلبات الأخرى
self.addEventListener('fetch', function(event) {
  // تجاهل طلبات غير GET والمتعلقة بـ chrome-extension
  if (event.request.method !== 'GET' || event.request.url.includes('chrome-extension')) {
    return;
  }
  
  // تحديد إذا كان الطلب لموارد ثابتة (CSS, JS, الصور، الخطوط)
  const isStaticResource = event.request.url.includes('/static/') || 
                          event.request.destination === 'style' ||
                          event.request.destination === 'script' ||
                          event.request.destination === 'image' ||
                          event.request.destination === 'font';
  
  // تحديد إذا كان طلب CDN
  const isCDNRequest = event.request.url.includes('cdn.jsdelivr.net') || 
                       event.request.url.includes('cdnjs.cloudflare.com') ||
                       event.request.url.includes('fonts.googleapis.com') ||
                       event.request.url.includes('fonts.gstatic.com');
  
  // للموارد الثابتة وطلبات CDN: استخدم استراتيجية الكاش أولاً
  if (isStaticResource || isCDNRequest) {
    event.respondWith(
      caches.match(event.request)
        .then(function(response) {
          // إذا وجد في الكاش، أعده مع تحديث الكاش في الخلفية
          if (response) {
            // تحديث الكاش في الخلفية
            fetchAndCache(event.request);
            return response;
          }
          
          // إذا لم يوجد في الكاش، أحضره من الشبكة ثم خزنه
          return fetchAndCache(event.request);
        })
        .catch(function() {
          // إذا فشل كل شيء، أعد رسالة مناسبة
          if (isStaticResource) {
            return new Response('الملف غير متوفر حالياً', {
              status: 503,
              statusText: 'Service Unavailable',
              headers: new Headers({
                'Content-Type': 'text/plain; charset=utf-8'
              })
            });
          }
          return caches.match('/offline.html');
        })
    );
  } else {
    // للطلبات الديناميكية: استخدم استراتيجية الشبكة أولاً
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
              if (response) {
                return response;
              }
              return caches.match('/offline.html');
            });
        })
    );
  }
});

// دمسة مساعدة لجلب الطلب وتخزينه في الكاش
function fetchAndCache(request) {
  return fetch(request)
    .then(function(response) {
      // تحقق إذا كانت الاستجابة صالحة
      if (!response || response.status !== 200 || response.type !== 'basic') {
        return response;
      }
      
      // استنساخ الاستجابة لأنها يمكن أن تستخدم مرة واحدة فقط
      const responseToCache = response.clone();
      
      caches.open(STATIC_CACHE)
        .then(function(cache) {
          cache.put(request, responseToCache);
        });
      
      return response;
    });
}

// باقي الأحداث (الرسائل، المزامنة، الإشعارات) تبقى كما هي
self.addEventListener('message', function(event) {
  if (event.data.action === 'skipWaiting') {
    self.skipWaiting();
  }
});

self.addEventListener('sync', function(event) {
  if (event.tag === 'background-sync') {
    event.waitUntil(doBackgroundSync());
  }
});

async function doBackgroundSync() {
  console.log('Service Worker: مزامنة في الخلفية');
}

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