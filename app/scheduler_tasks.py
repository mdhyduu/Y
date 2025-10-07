# app/scheduler_tasks.py
from datetime import datetime, timedelta
from .models import OrderStatusNote, OrderStatus, SallaOrder, db
import logging

logger = logging.getLogger('salla_app')

def check_and_update_late_orders_for_store(store_id):
    """فحص الطلبات المتأخرة لمتجر محدد"""
    try:
        # حساب التاريخ قبل يومين
        two_days_ago = datetime.utcnow() - timedelta(days=3)
        
        logger.info(f"🔍 فحص الطلبات المتأخرة للمتجر {store_id}")
        
        late_orders_count = 0
        
        # البحث عن حالة "قيد التنفيذ" في OrderStatus لهذا المتجر المحدد
        processing_status = OrderStatus.query.filter(
            OrderStatus.store_id == store_id,
            (OrderStatus.slug == 'in_progress') | 
            (OrderStatus.name.contains('قيد التنفيذ'))
        ).first()
         
        if not processing_status:
            logger.warning(f"⚠️ لم يتم العثور على حالة 'قيد التنفيذ' في المتجر {store_id}")
            return 0
        
        # البحث عن طلبات Salla في هذا المتجر المحدد
        late_salla_orders = SallaOrder.query.filter(
            SallaOrder.store_id == store_id,
            SallaOrder.status_id == processing_status.id,
            SallaOrder.created_at <= two_days_ago
        ).all()
        
        logger.info(f"📊 وجد {len(late_salla_orders)} طلب في حالة قيد التنفيذ منذ أكثر من يومين")
        
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
                logger.info(f"⏰ تم تعيين حالة متأخر تلقائياً للطلب {order.id}")
        
        if late_orders_count > 0:
            db.session.commit()
            logger.info(f"🎯 تم تحديث {late_orders_count} طلب إلى حالة متأخر في المتجر {store_id}")
        else:
            logger.info(f"✅ لا توجد طلبات تحتاج تحديث في المتجر {store_id}")
        
        return late_orders_count
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ خطأ في فحص الطلبات المتأخرة للمتجر {store_id}: {str(e)}")
        return 0
        
        
def handle_order_completion(store_id, order_id, new_status_slug):
    """معالجة اكتمال الطلب وإزالة الحالات المتأخرة"""
    try:
        logger.info(f"🔍 معالجة اكتمال الطلب {order_id} - الحالة الجديدة: {new_status_slug}")
        
        # قائمة بجميع الحالات التي تعتبر "مكتملة"
        completed_statuses = [
            'completed', 'delivered', 'delivering', 'shipped', 
            'منتهي', 'مستلم', 'canceled', 'منفذ', 'منفذة'
        ]
        
        # التحقق إذا الحالة الجديدة تعتبر حالة اكتمال
        is_completed = any(completed in new_status_slug for completed in completed_statuses)
        
        if is_completed:
            # البحث عن حالة "متأخر" وحذفها
            late_status_note = OrderStatusNote.query.filter_by(
                order_id=order_id,
                status_flag='late'
            ).first()
            
            if late_status_note:
                db.session.delete(late_status_note)
                db.session.commit()
                logger.info(f"✅ تم إزالة حالة المتأخر للطلب {order_id} بعد اكتماله")
                return True
            else:
                logger.info(f"ℹ️ لا توجد حالة متأخر لإزالتها للطلب {order_id}")
                return False
        else:
            logger.info(f"ℹ️ الطلب {order_id} ليس في حالة اكتمال - الحالة: {new_status_slug}")
            return False
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ خطأ في معالجة اكتمال الطلب {order_id}: {str(e)}")
        return False