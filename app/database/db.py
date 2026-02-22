from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import Report, User
import datetime

async def get_or_create_user(session: AsyncSession, telegram_id: int, referrer_id: int = None) -> User:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    # Если пользователя НЕТ в базе - создаем и засчитываем реферала
    if not user:
        user = User(
            telegram_id=telegram_id,
            referrer_id=referrer_id if referrer_id != telegram_id else None
        )
        session.add(user)
        
        # Логика реферальной системы
        if referrer_id and referrer_id != telegram_id:
            ref_result = await session.execute(select(User).where(User.telegram_id == referrer_id))
            referrer = ref_result.scalar_one_or_none()
            
            if referrer:
                referrer.referrals_count += 1
                
                # Каждые 5 приглашенных = VIP на 3 дня
                if referrer.referrals_count % 5 == 0:
                    now = datetime.datetime.utcnow()
                    # Если VIP уже есть, просто продлеваем его. Если нет - даем от текущего времени
                    current_vip = referrer.vip_until if (referrer.vip_until and referrer.vip_until > now) else now
                    referrer.vip_until = current_vip + datetime.timedelta(days=3)
                    
        await session.commit()
    return user

async def update_user_rating(session: AsyncSession, user_id: int, score: int):
    user = await get_or_create_user(session, user_id)
    # Формула пересчета среднего значения
    new_rating = ((user.rating * user.rating_count) + score) / (user.rating_count + 1)
    
    user.rating = round(new_rating, 2)
    user.rating_count += 1
    await session.commit()

async def add_report_and_check_ban(session: AsyncSession, reported_id: int, reporter_id: int, reason: str, ban_times: dict):
    # Добавляем запись о жалобе в таблицу reports
    new_report = Report(reporter_id=reporter_id, reported_id=reported_id, reason=reason)
    session.add(new_report)
    
    # Находим нарушителя и увеличиваем ему счетчик strikes
    user_result = await session.execute(select(User).where(User.telegram_id == reported_id))
    user = user_result.scalar_one_or_none()
    
    if user:
        user.strikes += 1  # Используем твое поле strikes
        report_count = user.strikes
        
        if report_count >= 5:
            user.is_banned = True
            await session.commit()
            return {"is_banned": True, "permanent": True}
            
        duration_sec = ban_times.get(report_count, 0)
        if duration_sec > 0:
            user.ban_until = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_sec)
            await session.commit()
            return {"is_banned": True, "duration": duration_sec // 60}
            
    await session.commit()
    return {"is_banned": False}

async def is_user_banned(session, user_id: int):
    result = await session.execute(select(User).where(User.telegram_id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        return None
        
    # 1. Проверяем вечный бан
    if user.is_banned:
        return "permanent"
        
    # 2. Проверяем временный бан
    if user.ban_until and user.ban_until > datetime.datetime.utcnow():
        return user.ban_until
        
    return None