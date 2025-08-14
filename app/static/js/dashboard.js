document.addEventListener('DOMContentLoaded', function () {
    const dashboard = document.getElementById('orders-dashboard');
    if (!dashboard) return;

    // --- قراءة البيانات من الـ HTML ---
    const SYNC_URL = dashboard.dataset.syncUrl;
    const ASSIGN_URL = dashboard.dataset.assignUrl;
    const INDEX_URL = dashboard.dataset.indexUrl;
    const CSRF_TOKEN = dashboard.dataset.csrfToken;
    const CURRENT_USER_ID = dashboard.dataset.userId;
    const IS_REVIEWER = JSON.parse(dashboard.dataset.isReviewer);
    const EMPLOYEES = IS_REVIEWER ? JSON.parse(dashboard.dataset.employees) : [];
    
    // --- تهيئة المكتبات ---
    dayjs.extend(dayjs_plugin_relativeTime);
    dayjs.locale('ar');
    
    // --- تهيئة الأدوات (Tooltips) ---
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        // تأكد من وجود مكتبة bootstrap
        if (typeof bootstrap !== 'undefined') {
            return new bootstrap.Tooltip(tooltipTriggerEl);
        }
    });

    // --- الوظائف المساعدة ---
    function updateTimestamps() {
        document.querySelectorAll('.time-ago').forEach(el => {
            const dateStr = el.getAttribute('title');
            if (dateStr) {
                el.textContent = dayjs(dateStr).fromNow();
            }
        });
    }

    function showToast(icon, title, text = '', timer = 3000) {
        const Toast = Swal.mixin({
            toast: true,
            position: 'top-end',
            showConfirmButton: false,
            timer: timer,
            timerProgressBar: true,
            didOpen: (toast) => {
                toast.addEventListener('mouseenter', Swal.stopTimer);
                toast.addEventListener('mouseleave', Swal.resumeTimer);
            }
        });
        
        Toast.fire({ icon, title, text });
    }
    
    function updateAssignButtonState() {
        if (!IS_REVIEWER) return;
        const checkedCount = $('.order-checkbox:checked').length;
        $('#selected-orders-count').text(checkedCount);
        $('#assign-orders-btn').prop('disabled', checkedCount === 0);
    }

    function buildQueryParams() {
        const params = new URLSearchParams(window.location.search);
        
        const filters = {
            'status': $('#filter-order-status').val(),
            'search': $('#search-order-id').val(),
            'date_from': $('#date-from').val(),
            'date_to': $('#date-to').val(),
            'custom_status': $('#filter-custom-status').val(),
            // ملاحظة: تم تغيير اسم البارامتر لتجنب التعارض مع فلتر الحالة الرئيسي
            'special_status': $('#filter-special-status').val(), 
            'employee': IS_REVIEWER ? $('#filter-employee').val() : null
        };
        
        for (const [key, value] of Object.entries(filters)) {
            if (value) {
                params.set(key, value);
            } else {
                params.delete(key);
            }
        }
        
        return params.toString();
    }
    
    function reloadOrders(showLoading = true) {
        if (showLoading) $('#table-loading').removeClass('d-none');
        const queryParams = buildQueryParams();
        window.location.href = `${INDEX_URL}?${queryParams}`;
    }

    function getEmployeeOptions() {
        const options = { '': 'اختر موظفًا...' };
        EMPLOYEES.forEach(employee => {
            if (employee.role === 'general') {
                options[employee.id] = employee.email;
            }
        });
        return options;
    }

    function handleAssignment(orderIds) {
        Swal.fire({
            title: `إسناد (${orderIds.length}) طلب`,
            text: `اختر الموظف الذي سيتم إسناد الطلبات إليه.`,
            input: 'select',
            inputOptions: getEmployeeOptions(),
            inputPlaceholder: 'اختر موظفًا...',
            showCancelButton: true,
            confirmButtonText: 'تأكيد الإسناد',
            cancelButtonText: 'إلغاء',
            showLoaderOnConfirm: true,
            customClass: {
                confirmButton: 'btn btn-primary',
                cancelButton: 'btn btn-outline-secondary ms-2'
            },
            buttonsStyling: false,
            inputValidator: (value) => !value && 'يجب اختيار موظف للإسناد',
            preConfirm: (employeeId) => {
                return $.ajax({
                    url: ASSIGN_URL,
                    type: "POST",
                    contentType: "application/json",
                    headers: { 'X-CSRFToken': CSRF_TOKEN },
                    data: JSON.stringify({
                        employee_id: employeeId,
                        order_ids: orderIds,
                        current_user_id: CURRENT_USER_ID
                    }),
                }).fail((xhr) => {
                    const errorMsg = xhr.responseJSON?.error || 'فشل الإسناد لسبب غير معروف.';
                    Swal.showValidationMessage(`فشل الطلب: ${errorMsg}`);
                });
            },
            allowOutsideClick: () => !Swal.isLoading()
        }).then((result) => {
            if (result.isConfirmed) {
                if (result.value.success) {
                    showToast('success', 'تم الإسناد بنجاح!', result.value.message);
                    setTimeout(() => reloadOrders(false), 1500);
                } else {
                    showToast('error', 'فشل الإسناد', result.value.error, 4000);
                }
            }
        });
    }

    // --- معالجات الأحداث ---

    // 1. مزامنة الطلبات
    $('#sync-orders-btn').on('click', function() {
        const btn = $(this);
        const icon = btn.find('i');
        const progressContainer = $('#sync-progress');
        const progressBar = progressContainer.find('.progress-bar');
        const detailsText = progressContainer.find('.sync-details');

        btn.prop('disabled', true);
        icon.addClass('fa-spin');
        btn.find('.sync-text').text('جاري المزامنة...');
        progressContainer.removeClass('d-none');
        progressBar.css('width', '10%');
        detailsText.text('جاري الاتصال بالخادم...');

        progressBar.animate({ width: "70%" }, 1500);

        $.ajax({
            url: SYNC_URL,
            type: "POST",
            contentType: "application/json",
            headers: { 'X-CSRFToken': CSRF_TOKEN }
        })
        .done(function(response) {
            progressBar.css('width', '100%');
            detailsText.text(response.message || 'اكتملت العملية.');
            if (response.success) {
                showToast('success', 'نجحت المزامنة!', response.message);
                setTimeout(() => reloadOrders(false), 2000);
            } else {
                showToast('error', 'فشلت المزامنة', response.error, 5000);
                btn.prop('disabled', false).find('.sync-text').text('مزامنة الطلبات');
                icon.removeClass('fa-spin');
                progressContainer.addClass('d-none');
            }
        })
        .fail(function(xhr) {
            progressBar.css('width', '100%');
            const errorMsg = xhr.responseJSON?.error || 'فشل الاتصال بالخادم.';
            showToast('error', 'حدث خطأ فادح', errorMsg, 5000);
            btn.prop('disabled', false).find('.sync-text').text('مزامنة الطلبات');
            icon.removeClass('fa-spin');
            progressContainer.addClass('d-none');
        });
    });

    if (IS_REVIEWER) {
        // 2. تحديد الطلبات (الكل أو فردي)
        $('#select-all').on('change', function() {
            const isChecked = this.checked;
            $('.order-checkbox').prop('checked', isChecked);
            $('.order-checkbox').closest('tr').toggleClass('selected-row', isChecked);
            updateAssignButtonState();
        });

        $('.order-checkbox').on('change', function() {
            $('#select-all').prop('checked', $('.order-checkbox:checked').length === $('.order-checkbox').length);
            $(this).closest('tr').toggleClass('selected-row', this.checked);
            updateAssignButtonState();
        });

        // 3. إسناد الطلبات المحددة
        $('#assign-orders-btn').on('click', function() {
            const orderIds = $('.order-checkbox:checked').map((_, el) => $(el).val()).get();
            if (orderIds.length > 0) handleAssignment(orderIds);
        });
        
        // 4. إسناد سريع لطلب واحد
        $('.assign-single-btn').on('click', function() {
            const orderId = $(this).data('order-id');
            handleAssignment([orderId]);
        });
    }

    // 5. تطبيق الفلاتر
    $('#advanced-filters').on('submit', e => {
        e.preventDefault();
        reloadOrders();
    });
    
    $('#search-order-id').on('input', _.debounce(() => reloadOrders(), 500));
    
    $('#filter-order-status, #filter-employee, #filter-custom-status, #filter-special-status').on('change', _.debounce(() => reloadOrders(), 300));
    
    // 6. إعادة تعيين الفلاتر
    $('#reset-filters, .btn-primary[id="reset-filters"]').on('click', () => {
        window.location.href = INDEX_URL;
    });

    // --- التشغيل عند تحميل الصفحة ---
    updateTimestamps();
    if (IS_REVIEWER) updateAssignButtonState();
    
    setInterval(updateTimestamps, 60000); // تحديث التواريخ كل دقيقة
});