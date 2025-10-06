# app/scheduler_tasks.py
from datetime import datetime, timedelta
from .models import OrderStatusNote, OrderStatus, SallaOrder, db
import logging

logger = logging.getLogger('salla_app')

def check_and_update_late_orders():
    """فحص الطلبات المتأخرة"""
    try:
        # حساب التاريخ قبل يومين
        two_days_ago = datetime.utcnow() - timedelta(days=2)
        
        logger.info(f"🔍 بدء فحص الطلبات المتأخرة - البحث في OrderStatus قبل: {two_days_ago}")
        
        late_orders_count = 0
        processed_orders = []
        
        # البحث عن حالة "قيد التنفيذ" في OrderStatus
        processing_status = OrderStatus.query.filter(
            (OrderStatus.slug == 'in_progress') | 
            (OrderStatus.name.contains('قيد التنفيذ'))
        ).first()
        
        if not processing_status:
            logger.warning("⚠️ لم يتم العثور على حالة 'قيد التنفيذ' في OrderStatus")
            return 0
        
        logger.info(f"✅ وجدت حالة قيد التنفيذ: {processing_status.name} (ID: {processing_status.id})")
        
        # البحث عن طلبات Salla التي في حالة "قيد التنفيذ" منذ أكثر من يومين
        late_salla_orders = SallaOrder.query.filter(
            SallaOrder.status_id == processing_status.id,
            SallaOrder.created_at <= two_days_ago
        ).all()
        
        logger.info(f"📊 وجد {len(late_salla_orders)} طلب Salla في حالة قيد التنفيذ منذ أكثر من يومين")
        
        for order in late_salla_orders:
            # التحقق من عدم وجود حالة "متأخر" مسبقاً
            existing_late_status = OrderStatusNote.query.filter_by(
                order_id=order.id,
                status_flag='late'
            ).first()
            
            if not existing_late_status:
                # إضافة حالة "متأخر" تلقائياً
                late_note = OrderStatusNote(
                    order_id=order.id,
                    status_flag='late',
                    note=f'تم تعيين الحالة تلقائياً بسبب تأخر الطلب منذ {order.created_at.strftime("%Y-%m-%d %H:%M")}'
                )
                db.session.add(late_note)
                late_orders_count += 1
                processed_orders.append(order.id)
                logger.info(f"⏰ تم تعيين حالة متأخر تلقائياً للطلب {order.id}")
        
        if late_orders_count > 0:
            db.session.commit()
            logger.info(f"🎯 تم تحديث {late_orders_count} طلب Salla إلى حالة متأخر: {processed_orders}")
        else:
            logger.info("✅ لا توجد طلبات Salla تحتاج تحديث")
        
        return late_orders_count
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ خطأ في فحص الطلبات المتأخرة: {str(e)}")
        return 0