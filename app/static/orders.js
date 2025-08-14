// orders.js - JavaScript للتعامل مع الطلبات على جانب العميل

// تنسيق التاريخ
function formatDate(dateStr) {
    if (!dateStr) return 'غير معروف';
    
    try {
        const dt = new Date(dateStr);
        if (isNaN(dt.getTime())) return dateStr;
        
        return dt.toLocaleString('ar-SA', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (e) {
        console.error('Error formatting date:', e);
        return dateStr;
    }
}

// إنشاء باركود على العميل باستخدام مكتبة JsBarcode
function generateBarcode(orderId, elementId) {
    try {
        JsBarcode(`#${elementId}`, orderId.toString(), {
            format: 'CODE128',
            displayValue: false,
            width: 1.5,
            height: 50,
            margin: 10
        });
        return true;
    } catch (e) {
        console.error('Error generating barcode:', e);
        return false;
    }
}

// معالجة بيانات الطلب للعرض
function processOrderData(orderData) {
    if (!orderData) return null;
    
    const order = orderData.data || {};
    const customer = order.customer || {};
    const customerNameParts = (customer.name || '').split(' ');
    const firstName = customerNameParts[0] || '';
    const lastName = customerNameParts.slice(1).join(' ') || '';
    
    // معالجة العناصر
    const items = (order.items || []).map(item => {
        const product = item.product || {};
        return {
            name: item.name || '',
            sku: item.sku || '',
            quantity: item.quantity || 0,
            price: {
                amount: (item.amounts || {}).total?.amount || 0,
                currency: item.currency || 'SAR'
            },
            product: {
                image: {
                    url: product.thumbnail || '',
                    status: product.status || '',
                    is_available: product.is_available || false,
                    regular_price: {
                        amount: (product.regular_price || {}).amount || 0,
                        currency: (product.regular_price || {}).currency || 'SAR'
                    },
                    promotion: product.promotion || {},
                    url: product.url || ''
                }
            }
        };
    });
    
    // معالجة المبالغ
    const amounts = order.amounts || {};
    const discounts = amounts.discounts || [];
    const totalDiscount = discounts.reduce((sum, d) => sum + (d.discount || 0), 0);
    
    return {
        id: order.id,
        reference_id: order.reference_id || order.id,
        status: {
            name: (order.status || {}).name || 'غير معروف',
            slug: (order.status || {}).slug || 'unknown'
        },
        created_at: formatDate((order.date || {}).date),
        payment_method: {
            name: order.payment_method || 'غير محدد'
        },
        amount: {
            sub_total: {
                amount: (amounts.sub_total || {}).amount || 0,
                currency: (amounts.sub_total || {}).currency || 'SAR'
            },
            shipping: {
                amount: (amounts.shipping_cost || {}).amount || 0,
                currency: (amounts.shipping_cost || {}).currency || 'SAR'
            },
            discount: {
                amount: totalDiscount,
                currency: 'SAR'
            },
            total: {
                amount: (amounts.total || {}).amount || 0,
                currency: (amounts.total || {}).currency || 'SAR'
            }
        },
        customer: {
            first_name: firstName,
            last_name: lastName,
            email: customer.email || 'غير متوفر',
            phone: `${customer.mobile_code || ''}${customer.mobile || ''}`
        },
        items: {
            data: items
        }
    };
}

// تحديث حالة الطلب (سيمول الاتصال بالخادم)
async function updateOrderStatus(orderId, newStatus, note = '') {
    if (!orderId || !newStatus) {
        showAlert('يجب اختيار حالة جديدة', 'error');
        return false;
    }
    
    try {
        // هنا سيكون الاتصال الفعلي بالخادم باستخدام fetch
        // لكننا سنقوم بمحاكاة العملية للتوضيح
        const response = await mockApiCall({
            url: `/orders/${orderId}/status`,
            method: 'POST',
            data: {
                slug: newStatus,
                note: note
            }
        });
        
        if (response.success) {
            showAlert('تم تحديث حالة الطلب بنجاح', 'success');
            
            // تحديث الواجهة دون إعادة تحميل الصفحة
            const statusElement = document.querySelector(`.order-status[data-order="${orderId}"]`);
            if (statusElement) {
                statusElement.textContent = getStatusName(newStatus);
                statusElement.className = `order-status status-${newStatus}`;
            }
            
            return true;
        } else {
            showAlert(response.error || 'حدث خطأ أثناء تحديث الحالة', 'error');
            return false;
        }
    } catch (error) {
        console.error('Error updating order status:', error);
        showAlert('حدث خطأ غير متوقع', 'error');
        return false;
    }
}

// دالة مساعدة لعرض التنبيهات
function showAlert(message, type = 'info') {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.textContent = message;
    
    const container = document.querySelector('.alerts-container') || document.body;
    container.prepend(alertDiv);
    
    setTimeout(() => {
        alertDiv.remove();
    }, 5000);
}

// دالة مساعدة للحصول على اسم الحالة
function getStatusName(slug) {
    const statusMap = {
        'pending': 'قيد الانتظار',
        'processing': 'قيد المعالجة',
        'shipped': 'تم الشحن',
        'delivered': 'تم التسليم',
        'cancelled': 'ملغي',
        'refunded': 'تم الاسترداد'
    };
    
    return statusMap[slug] || slug;
}

// محاكاة لاستدعاء API
async function mockApiCall({ url, method, data }) {
    console.log(`Mock API call: ${method} ${url}`, data);
    
    // محاكاة تأخير الشبكة
    await new Promise(resolve => setTimeout(resolve, 800));
    
    // في حالة حقيقية، نستخدم fetch:
    /*
    const response = await fetch(url, {
        method,
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${getAccessToken()}`
        },
        body: JSON.stringify(data)
    });
    
    return await response.json();
    */
    
    // محاكاة نجاح العملية
    return {
        success: true,
        data: {
            id: data.slug,
            updated_at: new Date().toISOString()
        }
    };
}

// تهيئة الصفحة عند التحميل
document.addEventListener('DOMContentLoaded', function() {
    // إنشاء الباركود لكل طلب
    document.querySelectorAll('.order-barcode').forEach(el => {
        const orderId = el.dataset.orderId;
        if (orderId) {
            generateBarcode(orderId, `barcode-${orderId}`);
        }
    });
    
    // معالجة نماذج تحديث الحالة
    document.querySelectorAll('.update-status-form').forEach(form => {
        form.addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const orderId = form.dataset.orderId;
            const statusSelect = form.querySelector('select[name="status_slug"]');
            const noteInput = form.querySelector('textarea[name="note"]');
            
            if (orderId && statusSelect) {
                const status = statusSelect.value;
                const note = noteInput ? noteInput.value : '';
                
                const success = await updateOrderStatus(orderId, status, note);
                if (success) {
                    form.reset();
                }
            }
        });
    });
    
    // البحث والتصفية المحلية
    const searchInput = document.querySelector('#orders-search');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            const searchTerm = this.value.toLowerCase();
            document.querySelectorAll('.order-item').forEach(item => {
                const text = item.textContent.toLowerCase();
                item.style.display = text.includes(searchTerm) ? '' : 'none';
            });
        });
    }
});

// تصدير الدوال للاختبار إذا لزم الأمر
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        formatDate,
        generateBarcode,
        processOrderData,
        updateOrderStatus,
        showAlert
    };
}