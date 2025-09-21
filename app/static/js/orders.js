$(document).ready(function () {
    // --- المتغيرات العامة ---
    const csrfToken = "{{ csrf_token() }}";
    let currentSelectedOrders = [];
    
    // --- تهيئة المكتبات ---
    dayjs.extend(dayjs_plugin_relativeTime);
    dayjs.locale('ar');
    
    // --- تهيئة الأدوات ---
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
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

    function showSweetAlert(icon, title, text = '', timer = 3000) {
        const Toast = Swal.mixin({
            toast: true,
            position: 'top-end',
            showConfirmButton: false,
            timer: timer,
            timerProgressBar: true,
            didOpen: (toast) => {
                toast.addEventListener('mouseenter', Swal.stopTimer)
                toast.addEventListener('mouseleave', Swal.resumeTimer)
            }
        });
        
        Toast.fire({
            icon: icon,
            title: title,
            text: text
        });
    }
    
    function showConfirmDialog(title, text, confirmText, cancelText) {
        return Swal.fire({
            title: title,
            text: text,
            icon: 'question',
            showCancelButton: true,
            confirmButtonColor: '#3085d6',
            cancelButtonColor: '#d33',
            confirmButtonText: confirmText,
            cancelButtonText: cancelText,
            customClass: {
                confirmButton: 'btn btn-primary',
                cancelButton: 'btn btn-outline-secondary ms-2'
            },
            buttonsStyling: false
        });
    }
    
    function updateAssignButtonState() {
        const checkedCount = $('.order-checkbox:checked').length;
        $('#selected-orders-count').text(checkedCount);
        $('#selected-orders-count-2').text(checkedCount);
        $('#selected-orders-count-print').text(checkedCount);
        $('#selected-orders-count-html').text(checkedCount);
        
        const buttons = $('#assign-orders-btn, #change-status-btn, #print-orders-btn, #download-html-btn, #quickListViewBtn');
        buttons.prop('disabled', checkedCount === 0);
        
        if (checkedCount > 0) {
            buttons.addClass('pulse-effect');
            setTimeout(() => buttons.removeClass('pulse-effect'), 1000);
        }
    }
    
    function buildQueryParams() {
        const params = new URLSearchParams();
        
        const status = $('#filter-order-status').val();
        const search = $('#search-order-id').val();
        const dateFrom = $('#date-from').val();
        const dateTo = $('#date-to').val();
        const perPage = $('#per-page-select').val();
        
        if (status) params.set('status', status);
        if (search) params.set('search', search);
        if (dateFrom) params.set('date_from', dateFrom);
        if (dateTo) params.set('date_to', dateTo);
        if (perPage) params.set('per_page', perPage);
        
        {% if is_reviewer %}
        const employee = $('#filter-employee').val();
        if (employee) params.set('employee', employee);
        {% endif %}
        
        const customStatus = $('#filter-custom-status').val();
        if (customStatus) params.set('custom_status', customStatus);
        
        {% if not is_reviewer %}
        const specialStatus = $('#filter-special-status').val();
        if (specialStatus) params.set('status', specialStatus);
        {% endif %}
        
        return params.toString();
    }
    
    function reloadOrders(showLoading = true) {
        if (showLoading) {
            $('#table-loading').removeClass('d-none');
        }
        
        const queryParams = buildQueryParams();
        window.location.href = `?${queryParams}`;
    }
    
    function toggleQuickListButton() {
        const selectedOrders = document.querySelectorAll('input.order-checkbox:checked');
        document.getElementById('quickListViewBtn').disabled = selectedOrders.length === 0;
    }
    
    function updateQuickListSelectionCount() {
        const selectedCount = document.querySelectorAll('#quickListContent input.quick-list-order:checked').length;
        document.getElementById('selectedOrdersCountQuick').textContent = `${selectedCount} طلب محدد`;

        const assignBtn = document.getElementById('quickListAssignBtn');
        if (assignBtn) {
            assignBtn.disabled = selectedCount === 0;
        }

        const totalOrders = document.querySelectorAll('#quickListContent input.quick-list-order').length;
        const selectAll = document.getElementById('selectAllQuickList');
        if (selectAll && totalOrders > 0) {
            selectAll.checked = selectedCount === totalOrders;
            selectAll.indeterminate = selectedCount > 0 && selectedCount < totalOrders;
        }
    }
    
    function syncData(url, successMessage, errorMessage) {
        const btn = $(this);
        const icon = btn.find('i');
        const progressContainer = $('#sync-progress');
        const progressBar = progressContainer.find('.progress-bar');
        const detailsText = progressContainer.find('.sync-details');

        btn.prop('disabled', true);
        icon.addClass('fa-spin');
        progressContainer.removeClass('d-none');
        progressBar.css('width', '10%');
        detailsText.text('جاري الاتصال بالخادم...');

        progressBar.animate({ width: "70%" }, 1500);

        $.ajax({
            url: url,
            type: "POST",
            contentType: "application/json",
            data: JSON.stringify({}),
            headers: { 'X-CSRFToken': csrfToken }
        })
        .done(function(response) {
            progressBar.css('width', '100%');
            if (response.success) {
                detailsText.text(response.message);
                showSweetAlert('success', successMessage, response.message);
                setTimeout(() => location.reload(), 2000);
            } else {
                showSweetAlert('error', errorMessage, response.error, 5000);
                btn.prop('disabled', false);
                icon.removeClass('fa-spin');
                progressContainer.addClass('d-none');
            }
        })
        .fail(function(xhr) {
            progressBar.css('width', '100%');
            const errorMsg = xhr.responseJSON?.error || 'فشل الاتصال بالخادم. تحقق من الشبكة.';
            showSweetAlert('error', 'حدث خطأ فادح', errorMsg, 5000);
            btn.prop('disabled', false);
            icon.removeClass('fa-spin');
            progressContainer.addClass('d-none');
        });
    }
    
    function assignOrders(employeeId, orders) {
        return $.ajax({
            url: "{{ url_for('orders.assign_orders') }}",
            type: "POST",
            contentType: "application/json",
            headers: { 'X-CSRFToken': csrfToken },
            data: JSON.stringify({
                employee_id: employeeId,
                orders: orders,
                current_user_id: "{{ session.get('user_id') }}"
            })
        });
    }
    
    function getSelectedOrders() {
        return $('.order-checkbox:checked').map((_, el) => {
            const row = $(el).closest('tr');
            return {
                id: row.data('order-id'),
                type: row.data('order-type')
            };
        }).get();
    }

    // --- معالجات الأحداث ---
    
    // 1. مزامنة الطلبات والحالات
    $(document).on('click', '#sync-orders-btn', function() {
        syncData.call(
            this, 
            "{{ url_for('orders.sync_orders') }}", 
            'نجحت المزامنة!', 
            'فشلت المزامنة'
        );
    });
    
    $(document).on('click', '#sync-statuses-btn', function() {
        syncData.call(
            this, 
            "{{ url_for('orders.sync_order_statuses') }}", 
            'نجحت مزامنة الحالات!', 
            'فشلت مزامنة الحالات'
        );
    });
    
    // 2. تحديد الطلبات (الكل أو فردي)
    $(document).on('change', '#select-all, #select-all-header', function() {
        const isChecked = this.checked;
        $('.order-checkbox').prop('checked', isChecked);
        
        if (isChecked) {
            $('.order-checkbox').closest('tr').addClass('selected-row');
        } else {
            $('.order-checkbox').closest('tr').removeClass('selected-row');
        }
        
        updateAssignButtonState();
    });

    $(document).on('change', '.order-checkbox', function() {
        const allChecked = $('.order-checkbox:checked').length === $('.order-checkbox').length;
        $('#select-all, #select-all-header').prop('checked', allChecked);
        
        if ($(this).is(':checked')) {
            $(this).closest('tr').addClass('selected-row');
        } else {
            $(this).closest('tr').removeClass('selected-row');
        }
        
        updateAssignButtonState();
        toggleQuickListButton();
    });

    // 3. إسناد الطلبات المحددة
    $(document).on('click', '#assign-orders-btn', function() {
        const selectedOrders = getSelectedOrders();
        
        const employeeOptions = {
            '': 'اختر موظفًا...'
            {% for employee in employees %}
                {% if employee.role == 'general' %}
                    ,"{{ employee.id }}": "{{ employee.email }}"
                {% endif %}
            {% endfor %}
        };
        
        Swal.fire({
            title: 'إسناد الطلبات المحددة',
            html: `سيتم إسناد <span class="fw-bold text-primary">${selectedOrders.length}</span> طلب إلى الموظف المحدد.`,
            input: 'select',
            inputOptions: employeeOptions,
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
            inputValidator: (value) => {
                if (!value) return 'يجب اختيار موظف للإسناد';
            },
            preConfirm: (employeeId) => {
                return assignOrders(employeeId, selectedOrders)
                    .fail((xhr) => {
                        const errorMsg = xhr.responseJSON?.error || 'فشل الإسناد لسبب غير معروف.';
                        Swal.showValidationMessage(`فشل الطلب: ${errorMsg}`);
                    });
            },
            allowOutsideClick: () => !Swal.isLoading()
        }).then((result) => {
            if (result.isConfirmed && result.value.success) {
                showSweetAlert('success', 'تم الإسناد بنجاح!', result.value.message);
                setTimeout(() => location.reload(), 1500);
            }
        });
    });
    
    $(document).on('click', '.assign-single-btn', function(e) {
        e.stopPropagation();
        const orderId = $(this).data('order-id');
        const orderType = $(this).data('order-type');
        
        const employeeOptions = {
            '': 'اختر موظفًا...'
            {% for employee in employees %}
                {% if employee.role == 'general' %}
                    ,"{{ employee.id }}": "{{ employee.email }}"
                {% endif %}
            {% endfor %}
        };
        
        Swal.fire({
            title: 'إسناد الطلب #' + orderId,
            input: 'select',
            inputOptions: employeeOptions,
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
            inputValidator: (value) => {
                if (!value) return 'يجب اختيار موظف للإسناد';
            },
            preConfirm: (employeeId) => {
                return assignOrders(employeeId, [{ id: orderId, type: orderType }])
                    .fail((xhr) => {
                        const errorMsg = xhr.responseJSON?.error || 'فشل الإسناد لسبب غير معروف.';
                        Swal.showValidationMessage(`فشل الطلب: ${errorMsg}`);
                    });
            },
            allowOutsideClick: () => !Swal.isLoading()
        }).then((result) => {
            if (result.isConfirmed && result.value.success) {
                showSweetAlert('success', 'تم الإسناد بنجاح!', result.value.message);
                setTimeout(() => location.reload(), 1500);
            }
        });
    });
    
    // 4. تغيير حالة الطلبات
    $(document).on('click', '#change-status-btn', function() {
        const orderIds = $('.order-checkbox:checked').map((_, el) => $(el).val()).get();
        $('#selected-count-modal').text(orderIds.length);
        $('#changeStatusModal').modal('show');
    });
    
    $(document).on('click', '#confirm-status-change', function() {
        const orderIds = $('.order-checkbox:checked').map((_, el) => $(el).val()).get();
        const statusId = $('#status-select').val();
        const note = $('#status-note').val();
        
        if (!statusId) {
            showSweetAlert('warning', 'تحذير', 'يجب اختيار حالة');
            return;
        }
        
        $('#changeStatusModal').modal('hide');
        
        $.ajax({
            url: "{{ url_for('orders.bulk_update_status') }}",
            type: "POST",
            contentType: "application/json",
            headers: { 'X-CSRFToken': csrfToken },
            data: JSON.stringify({
                order_ids: orderIds,
                status_id: statusId,
                note: note
            }),
        })
        .done(function(response) {
            if (response.success) {
                showSweetAlert('success', 'نجح التحديث!', response.message);
                setTimeout(() => location.reload(), 1500);
            } else {
                showSweetAlert('error', 'فشل التحديث', response.error);
            }
        })
        .fail(function(xhr) {
            const errorMsg = xhr.responseJSON?.error || 'فشل الاتصال بالخادم';
            showSweetAlert('error', 'حدث خطأ', errorMsg);
        });
    });
    
    // 5. الفلاتر والبحث
    $(document).on('submit', '#advanced-filters', function(e) {
        e.preventDefault();
        reloadOrders();
    });
    
    $(document).on('input', '#search-order-id', _.debounce(function() {
        reloadOrders();
    }, 500));
    
    $(document).on('change', '#filter-order-status, #filter-employee, #filter-custom-status, #filter-special-status, #per-page-select', _.debounce(function() {
        reloadOrders();
    }, 300));
    
    // 6. إعادة تعيين الفلاتر
    $(document).on('click', '#reset-filters, #reset-filters-btn', function() {
        window.location.href = "{{ url_for('orders.index') }}";
    });

    // 7. النقر على صف الجدول للانتقال إلى التفاصيل
    $(document).on('click', '.clickable-row', function(e) {
        if ($(e.target).is('input, button, a, i') || $(e.target).closest('button, a, .actions-cell').length) {
            return;
        }
        
        const orderId = $(this).data('order-id');
        const orderType = $(this).data('order-type');
        
        let url;
        if (orderType === 'custom') {
            url = "{{ url_for('orders.custom_order_details', order_id=0) }}".replace('0', orderId);
        } else {
            url = "{{ url_for('orders.order_details', order_id=0) }}".replace('0', orderId);
        }
        
        window.location.href = url;
    });
    
    // 8. القائمة السريعة
    $(document).on('click', '#quickListViewBtn', function() {
        const selectedOrders = $('.order-checkbox:checked').map((_, el) => $(el).val()).get();
        
        if (selectedOrders.length === 0) {
            showSweetAlert('warning', 'تحذير', 'يرجى تحديد طلب واحد على الأقل');
            return;
        }

        currentSelectedOrders = selectedOrders;

        $('#quickListContent').html(`
            <div class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="sr-only">جاري التحميل...</span>
                </div>
                <p class="mt-2">جاري تحميل بيانات الطلبات...</p>
            </div>
        `);

        $('#quickListModal').modal('show');

        $.ajax({
            url: "{{ url_for('orders.get_quick_list_data') }}",
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            data: JSON.stringify({ order_ids: selectedOrders })
        })
        .done(function(data) {
            if (data.success) {
                let html = '';
                data.orders.forEach(order => {
                    html += `
<div class="card mb-3" data-order-id="${order.id}" data-order-type="${order.type}">
    <div class="card-header d-flex justify-content-between align-items-center">
        <div class="form-check">
            <input class="form-check-input quick-list-order" type="checkbox" 
                   value="${order.id}" id="quick-order-${order.id}" checked>
            <label class="form-check-label fw-bold" for="quick-order-${order.id}">
                الطلب #${order.reference_id}
            </label>
        </div>
        <span class="badge bg-${order.status_class || 'secondary'}">${order.status_text || 'غير معروف'}</span>
    </div>
    <div class="card-body">
        <div class="row">
    `;
                    
                    if (order.items && order.items.length > 0) {
                        order.items.forEach((item, index) => {
                            html += `
                                <div class="col-3 mb-3 text-center">
                                    <img src="${item.main_image || '/static/images/no-image.png'}" 
                                         alt="${item.name}" 
                                         class="img-fluid rounded border" 
                                         style="max-height: 100px; object-fit: cover;">
                                    <p class="mt-1 small text-truncate">${item.name}</p>
                                    <p class="text-muted small">الكمية: ${item.quantity}</p>
                                </div>
                            `;
                        });
                    } else {
                        html += `<div class="col-12"><p class="text-muted text-center">لا توجد منتجات في هذا الطلب</p></div>`;
                    }
                    
                    html += `
                        </div>
                    </div>
                </div>
                `;
                });
                
                $('#quickListContent').html(html);
                updateQuickListSelectionCount();
            } else {
                $('#quickListContent').html(`
                    <div class="alert alert-danger">${data.error || 'حدث خطأ أثناء جلب البيانات'}</div>
                `);
            }
        })
        .fail(function(error) {
            $('#quickListContent').html(`
                <div class="alert alert-danger">حدث خطأ في الاتصال: ${error.statusText}</div>
            `);
        });
    });
    
    // 9. تحديد الكل في القائمة السريعة
    $(document).on('change', '#selectAllQuickList', function() {
        const isChecked = this.checked;
        $('#quickListContent input.quick-list-order').prop('checked', isChecked);
        updateQuickListSelectionCount();
    });
    
    // 10. تحديث عدد التحديدات في القائمة السريعة
    $(document).on('change', '#quickListContent input.quick-list-order', function() {
        updateQuickListSelectionCount();
    });
    
    // 11. إسناد الطلبات من القائمة السريعة
    $(document).on('click', '#quickListAssignBtn', function() {
        const employeeId = $('#quickListAssignSelect').val();
        const selectedOrders = [];
        
        $('#quickListContent input.quick-list-order:checked').each(function() {
            const card = $(this).closest('.card');
            selectedOrders.push({
                id: card.data('order-id'),
                type: card.data('order-type')
            });
        });

        if (!employeeId) {
            showSweetAlert('warning', 'تحذير', 'يرجى اختيار موظف للإسناد');
            return;
        }

        if (selectedOrders.length === 0) {
            showSweetAlert('warning', 'تحذير', 'لم يتم تحديد أي طلبات للإسناد');
            return;
        }

        const assignBtn = $('#quickListAssignBtn');
        const originalText = assignBtn.html();
        assignBtn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i> جاري الإسناد');

        assignOrders(employeeId, selectedOrders)
            .done(function(data) {
                if (data.success) {
                    Swal.fire({
                        icon: 'success',
                        title: 'تم الإسناد بنجاح',
                        text: data.message || 'تم إسناد الطلبات إلى الموظف المحدد',
                        confirmButtonText: 'موافق',
                        customClass: {
                            confirmButton: 'btn btn-success'
                        }
                    }).then(() => {
                        $('#quickListModal').modal('hide');
                        location.reload();
                    });
                } else {
                    throw new Error(data.error || 'حدث خطأ غير متوقع أثناء الإسناد');
                }
            })
            .fail(function(error) {
                assignBtn.prop('disabled', false).html(originalText);
                const errorMsg = error.responseJSON?.error || error.statusText || 'حدث خطأ أثناء محاولة الإسناد';
                
                Swal.fire({
                    icon: 'error',
                    title: 'فشل الإسناد',
                    text: errorMsg,
                    confirmButtonText: 'موافق',
                    customClass: {
                        confirmButton: 'btn btn-danger'
                    }
                });
            });
    });
    
    // 12. تحميل HTML
    $(document).on('click', '#download-html-btn', function() {
        const selectedOrders = $('.order-checkbox:checked').map((_, el) => $(el).val()).get();
        
        if (selectedOrders.length === 0) {
            showSweetAlert('warning', 'تحذير', 'لم يتم تحديد أي طلبات للتحميل');
            return;
        }
        
        Swal.fire({
            title: 'تحميل الطلبات',
            html: `سيتم تحميل <span class="fw-bold text-primary">${selectedOrders.length}</span> طلب كملف HTML يعمل بدون اتصال`,
            icon: 'info',
            showCancelButton: true,
            confirmButtonText: 'موافق، تحميل HTML',
            cancelButtonText: 'إلغاء',
            customClass: {
                confirmButton: 'btn btn-primary',
                cancelButton: 'btn btn-outline-secondary ms-2'
            },
            buttonsStyling: false
        }).then((result) => {
            if (result.isConfirmed) {
                const downloadBtn = $('#download-html-btn');
                const originalText = downloadBtn.html();
                downloadBtn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin me-1"></i> جاري التجهيز');
                
                const downloadLink = document.createElement('a');
                downloadLink.href = `{{ url_for('orders.download_orders_html') }}?order_ids=${selectedOrders.join(',')}`;
                downloadLink.style.display = 'none';
                document.body.appendChild(downloadLink);
                downloadLink.click();
                document.body.removeChild(downloadLink);
                
                setTimeout(() => {
                    downloadBtn.prop('disabled', false).html(originalText);
                }, 2000);
            }
        });
    });
    
    // 13. تغيير الحالة من القائمة السريعة
    $(document).on('click', '#quickListChangeStatusBtn', function() {
        const selectedOrders = [];
        $('#quickListContent input.quick-list-order:checked').each(function() {
            selectedOrders.push($(this).val());
        });

        if (selectedOrders.length === 0) {
            showSweetAlert('warning', 'تحذير', 'يرجى تحديد طلب واحد على الأقل لتغيير الحالة');
            return;
        }

        $('#quickListModal').modal('hide');
        
        setTimeout(() => {
            $('#selected-count-modal').text(selectedOrders.length);
            $('#changeStatusModal').data('selected-orders', selectedOrders);
            $('#changeStatusModal').modal('show');
        }, 300);
    });

    // --- التشغيل عند تحميل الصفحة ---
    updateTimestamps();
    updateAssignButtonState();
    toggleQuickListButton();
    
    // تحديث التواريخ كل دقيقة
    setInterval(updateTimestamps, 60000);
    
    // تحسين تجربة الجوال
    if (window.innerWidth < 768) {
        $('.sidebar').removeClass('show');
        $('.sidebar__backdrop').removeClass('show');
    }
});