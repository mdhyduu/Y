// sw.js - إصدار متكامل للتخزين الكامل
const CACHE_NAME = 'dashboard-full-cache-v1';
const urlsToCache = [
  '/',
  '/orders',
  '/scan_barcode',
  '/manage_employee_status', 
  '/manage_note_status',
  '/dashboard',
  '/list_employees',
  '/list_products',
  '/link_store',
  '/logout',
  '/static/css/main.css',
  '/static/js/main.js',
  '/static/icons/icon-192x192.png',
  '/static/icons/s.png',

  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css',
  'https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&display=swap',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
  'https://code.jquery.com/jquery-3.6.0.min.js'
];

// تثبيت Service Worker
self.addEventListener('install', function(event) {
  self.skipWaiting(); // التفعيل الفوري
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function(cache) {
        console.log('Service Worker: تخزين جميع أصول التطبيق');
        return cache.addAll(urlsToCache).catch(error => {
          console.log('فشل في تخزين بعض الملفات:', error);
        });
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
            console.log('Service Worker: حذف الكاش القديم', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// إستراتيجية التخزين: التخزين أولاً مع التحديث من الشبكة
self.addEventListener('fetch', function(event) {
  // تجاهل طلبات غير GET
  if (event.request.method !== 'GET') return;
  
  event.respondWith(
    caches.match(event.request)
      .then(function(response) {
        // إذا وجد في الكاش، أعده مع التحديث في الخلفية
        if (response) {
          // تحديث الكاش في الخلفية
          fetchAndCache(event.request);
          return response;
        }
        
        // إذا لم يوجد، حمله من الشبكة وخزنه
        return fetchAndCache(event.request);
      })
      .catch(function() {
        // إذا فشل كل شيء، أعد رسالة عدم اتصال
        if (event.request.headers.get('accept').includes('text/html')) {
          return caches.match('/offline.html');
        }
        
        return new Response('عذراً، أنت غير متصل بالإنترنت', {
          status: 503,
          statusText: 'Service Unavailable',
          headers: new Headers({
            'Content-Type': 'text/plain; charset=utf-8'
          })
        });
      })
  );
});

// دالة مساعدة للجلب والتخزين
function fetchAndCache(request) {
  return fetch(request).then(function(response) {
    // تحقق إذا كان الرد صالح للتخزين
    if (!response || response.status !== 200 || response.type !== 'basic') {
      return response;
    }
    
    // استنساخ الرد
    var responseToCache = response.clone();
    
    caches.open(CACHE_NAME)
      .then(function(cache) {
        cache.put(request, responseToCache);
      });
    
    return response;
  });
}

// معالجة رسائل الخلفية
self.addEventListener('message', function(event) {
  if (event.data.action === 'skipWaiting') {
    self.skipWaiting();
  }
});