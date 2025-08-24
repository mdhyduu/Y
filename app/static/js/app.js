// تحميل المحتوى الديناميكي عند الاتصال بالإنترنت
function loadDynamicContent() {
  if (navigator.onLine) {
    // إظهار هيكل التحميل
    document.getElementById('skeleton-loader').style.display = 'block';
    document.getElementById('offline-message').style.display = 'none';
    
    // تحميل المحتوى الحقيقي
    fetchDynamicData()
      .then(data => {
        document.getElementById('skeleton-loader').style.display = 'none';
        renderDynamicContent(data);
      })
      .catch(error => {
        console.error('Error loading dynamic content:', error);
        document.getElementById('skeleton-loader').style.display = 'none';
        showOfflineData();
      });
  } else {
    showOfflineData();
  }
}

// محاولة إظهار البيانات المخزنة محليًا
function showOfflineData() {
  // هنا يمكنك محاولة جلب البيانات من التخزين المحلي
  const offlineData = localStorage.getItem('offlineData');
  if (offlineData) {
    try {
      const data = JSON.parse(offlineData);
      renderDynamicContent(data);
      document.getElementById('offline-message').style.display = 'block';
    } catch (e) {
      showNoDataMessage();
    }
  } else {
    showNoDataMessage();
  }
}

// عرض رسالة عدم وجود بيانات
function showNoDataMessage() {
  document.getElementById('dynamic-data').innerHTML = `
    <div class="alert alert-info">
      <i class="fas fa-info-circle"></i>
      لا تتوفر بيانات حالياً. يرجى الاتصال بالإنترنت لتحميل أحدث البيانات.
    </div>
  `;
}

// عند تحميل الصفحة
document.addEventListener('DOMContentLoaded', function() {
  // تحميل المحتوى الديناميكي بعد تحميل الهيكل
  setTimeout(loadDynamicContent, 0);
  
  // تحديث عند العودة للاتصال
  window.addEventListener('online', loadDynamicContent);
});