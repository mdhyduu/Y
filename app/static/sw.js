const CACHE_NAME = "pwa-shell-cache-v3";
const STATIC_CACHE = "pwa-static-cache-v3";
const DYNAMIC_CACHE = "pwa-dynamic-cache-v3";

// الملفات الثابتة (الهيكل الأساسي)
const staticAssets = [
  '/',
  '/static/css/main.css',
  '/static/css/bootstrap.rtl.min.css',
  '/static/css/font-awesome.min.css',
  '/static/js/bootstrap.bundle.min.js',
  '/static/js/jquery-3.6.0.min.js',
  '/static/js/app.js',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  '/static/icons/s.png',
  '/static/manifest.json',
  '/offline'  // صفحة خاصة لعدم الاتصال
];

// الصفحات التي نريد تخزين هيكلها فقط
const shellPages = [
  '/',
  '/dashboard',
  '/orders',
  '/manage_employee_status',
  '/manage_note_status',
  '/list_employees',
  '/link_store'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => {
        console.log('تخزين الملفات الثابتة');
        return cache.addAll(staticAssets);
      })
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cache => {
          if (cache !== STATIC_CACHE && cache !== DYNAMIC_CACHE) {
            console.log('حذف التخزين القديم:', cache);
            return caches.delete(cache);
          }
        })
      );
    })
  );
  return self.clients.claim();
});

self.addEventListener('fetch', event => {
  // تجاهل طلبات غير GET وطلبات أخرى غير هامة
  if (event.request.method !== 'GET' || 
      event.request.url.includes('/api/') ||
      event.request.url.includes('socket.io') ||
      event.request.url.includes('chrome-extension')) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then(response => {
      // إذا كان المطلب من الصفحات التي نريد تخزين هيكلها فقط
      if (shellPages.some(page => event.request.url.includes(page))) {
        return fetchPageWithFallback(event);
      }
      
      // للملفات الثابتة، نعيدها من التخزين إن وجدت
      if (response) {
        // نحدث التخزين في الخلفية
        updateCache(event.request);
        return response;
      }
      
      // للملفات الأخرى، نستخدم الشبكة مع وجود بديل
      return fetch(event.request)
        .then(res => {
          // نخزن في التخزين الديناميكي
          cacheDynamicData(event.request, res.clone());
          return res;
        })
        .catch(() => {
          // إذا فشلنا، نعيد صفحة عدم الاتصال
          return caches.match('/offline');
        });
    })
  );
});

// دالة خاصة للصفحات (تخزن الهيكل فقط)
function fetchPageWithFallback(event) {
  return fetch(event.request)
    .then(response => {
      // ننسخ الرد لأننا سنستخدمه مرتين
      const responseClone = response.clone();
      
      // نستخرج الهيكل فقط من الصفحة (بدون المحتوى الديناميكي)
      responseClone.text().then(html => {
        // هنا يمكنك معالجة HTML لاستخراج الهيكل فقط
        // هذا مثال مبسط - يمكنك استخدام تقنيات أكثر تطوراً
        const strippedHtml = removeDynamicContent(html);
        
        // نخزن الهيكل فقط
        caches.open(STATIC_CACHE).then(cache => {
          const strippedResponse = new Response(strippedHtml, {
            headers: response.headers,
            status: response.status,
            statusText: response.statusText
          });
          cache.put(event.request, strippedResponse);
        });
      });
      
      return response;
    })
    .catch(() => {
      // إذا فشل الاتصال، نعيد الهيكل المخزن
      return caches.match(event.request).then(response => {
        return response || caches.match('/offline');
      });
    });
}

// دالة لإزالة المحتوى الديناميكي (مثال مبسط)
function removeDynamicContent(html) {
  // هذه دالة مثالبة - يجب تكييفها حسب هيكل تطبيقك
  let cleanedHtml = html;
  
  // إزالة المحتوى الديناميكي (يجب تعديل هذا حسب هيكل صفحتك)
  cleanedHtml = cleanedHtml.replace(/<div class="dynamic-content">.*?<\/div>/gs, '');
  cleanedHtml = cleanedHtml.replace(/<div id="ajax-content">.*?<\/div>/gs, '');
  
  return cleanedHtml;
}

function updateCache(request) {
  return fetch(request).then(response => {
    if (response.status === 200) {
      caches.open(DYNAMIC_CACHE).then(cache => {
        cache.put(request, response);
      });
    }
    return response;
  });
}

function cacheDynamicData(request, response) {
  if (response.status === 200) {
    caches.open(DYNAMIC_CACHE).then(cache => {
      cache.put(request, response);
    });
  }
}