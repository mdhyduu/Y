# app/scheduler_tasks.py
from datetime import datetime, timedelta
from .models import OrderStatusNote, OrderStatus, SallaOrder, db
import logging

logger = logging.getLogger('salla_app')

def check_and_update_late_orders_for_store(store_id):
    """فحص الطلبات المتأخرة لمتجر محدد"""
    try:
        # حساب التواريخ
        two_days_ago = datetime.utcnow() - timedelta(days=2)
        three_days_ago = datetime.utcnow() - timedelta(days=3)
        
        logger.info(f"🔍 فحص الطلبات المتأخرة للمتجر {store_id}")
        
        late_orders_count = 0
        not_shipped_orders_count = 0
        
        # البحث عن حالة "قيد التنفيذ" في OrderStatus لهذا المتجر المحدد
        processing_status = OrderStatus.query.filter(
            OrderStatus.store_id == store_id,
            (OrderStatus.slug == 'in_progress') | 
            (OrderStatus.name.contains('قيد التنفيذ'))
        ).first()
        
        # البحث عن حالة "تم التنفيذ" في OrderStatus لهذا المتجر المحدد
        executed_status = OrderStatus.query.filter(
            OrderStatus.store_id == store_id,
            (OrderStatus.slug == 'completed') | 
            (OrderStatus.name.contains('تم التنفيذ'))
        ).first()
         
        if not processing_status:
            logger.warning(f"⚠️ لم يتم العثور على حالة 'قيد التنفيذ' في المتجر {store_id}")
        else:
            # البحث عن طلبات Salla في هذا المتجر المحدد بحالة "قيد التنفيذ" منذ أكثر من يومين
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
        
        # فحص الطلبات التي حالتها "تم التنفيذ" لأكثر من 3 أيام
        if not executed_status:
            logger.warning(f"⚠️ لم يتم العثور على حالة 'تم التنفيذ' في المتجر {store_id}")
        else:
            # البحث عن طلبات Salla في هذا المتجر المحدد بحالة "تم التنفيذ" منذ أكثر من 3 أيام
            executed_salla_orders = SallaOrder.query.filter(
                SallaOrder.store_id == store_id,
                SallaOrder.status_id == executed_status.id,
                SallaOrder.created_at <= three_days_ago
            ).all()
            
            logger.info(f"📦 وجد {len(executed_salla_orders)} طلب في حالة تم التنفيذ منذ أكثر من 3 أيام")
            
            for order in executed_salla_orders:
                # التحقق من عدم وجود حالة "لم يتم الشحن" مسبقاً
                existing_not_shipped_status = OrderStatusNote.query.filter_by(
                    order_id=order.id,
                    status_flag='not_shipped'
                ).first()
                
                if not existing_not_shipped_status:
                    # إضافة حالة "لم يتم الشحن" تلقائياً
                    not_shipped_note = OrderStatusNote(
                        order_id=order.id,
                        status_flag='not_shipped',
                        note=f'تم تعيين الحالة تلقائياً لأن الطلب في حالة تم التنفيذ منذ {order.created_at.strftime("%Y-%m-%d %H:%M")} ولم يتم شحنه'
                    )
                    db.session.add(not_shipped_note)
                    not_shipped_orders_count += 1
                    logger.info(f"🚫 تم تعيين حالة لم يتم الشحن تلقائياً للطلب {order.id}")
        
        if late_orders_count > 0 or not_shipped_orders_count > 0:
            db.session.commit()
            logger.info(f"🎯 تم تحديث {late_orders_count} طلب إلى حالة متأخر و {not_shipped_orders_count} طلب إلى حالة لم يتم الشحن في المتجر {store_id}")
        else:
            logger.info(f"✅ لا توجد طلبات تحتاج تحديث في المتجر {store_id}")
        
        return {
            'late_orders': late_orders_count,
            'not_shipped_orders': not_shipped_orders_count
        }
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ خطأ في فحص الطلبات المتأخرة للمتجر {store_id}: {str(e)}")
        return {'late_orders': 0, 'not_shipped_orders': 0}
        
        
def handle_order_completion(store_id, order_id, new_status_slug):
    """معالجة اكتمال الطلب وإزالة الحالات المتأخرة ولم يتم الشحن"""
    try:
        logger.info(f"🔍 معالجة اكتمال الطلب {order_id} - الحالة الجديدة: {new_status_slug}")
        
        # قائمة بجميع الحالات التي تعتبر "مكتملة"
        completed_statuses = [
            'completed', 'delivered', 'delivering', 'shipped', 
            'منتهي', 'مستلم', 'canceled', 'منفذ', 'منفذة'
        ]
        
        # قائمة بالحالات التي تزيل حالة "لم يتم الشحن"
        shipping_statuses = [
            'shipped', 'delivered', 'delivering'
        ]
        
        # التحقق إذا الحالة الجديدة تعتبر حالة اكتمال
        is_completed = any(completed in new_status_slug for completed in completed_statuses)
        
        # التحقق إذا الحالة الجديدة تعتبر حالة شحن
        is_shipping = any(shipping in new_status_slug for shipping in shipping_statuses)
        
        deleted_count = 0
        
        # إذا كانت الحالة مكتملة، نزيل حالة "متأخر"
        if is_completed:
            late_status_note = OrderStatusNote.query.filter_by(
                order_id=order_id,
                status_flag='late'
            ).first()
            
            if late_status_note:
                db.session.delete(late_status_note)
                deleted_count += 1
                logger.info(f"✅ تم إزالة حالة المتأخر للطلب {order_id} بعد اكتماله")
        
        # إذا كانت الحالة من حالات الشحن أو الإلغاء، نزيل حالة "لم يتم الشحن"
        if is_shipping or 'canceled' in new_status_slug:
            not_shipped_status_note = OrderStatusNote.query.filter_by(
                order_id=order_id,
                status_flag='not_shipped'
            ).first()
            
            if not_shipped_status_note:
                db.session.delete(not_shipped_status_note)
                deleted_count += 1
                logger.info(f"✅ تم إزالة حالة لم يتم الشحن للطلب {order_id} - الحالة: {new_status_slug}")
        
        if deleted_count > 0:
            db.session.commit()
            return True
        else:
            logger.info(f"ℹ️ لا توجد حالات متأخر أو لم يتم الشحن لإزالتها للطلب {order_id}")
            return False
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ خطأ في معالجة اكتمال الطلب {order_id}: {str(e)}")
        return False