# app/database/models.py
from datetime import datetime
from sqlalchemy import BigInteger, String, Float, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'
    
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rating: Mapped[float] = mapped_column(Float, default=5.0)
    rating_count: Mapped[int] = mapped_column(Integer, default=1)
    vip_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # Реферальная система
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey('users.telegram_id'), nullable=True)
    referrals_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Система банов
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False) # <--- ДОБАВИЛИ ЭТО
    ban_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    strikes: Mapped[int] = mapped_column(Integer, default=0) # Будем использовать для счета жалоб
    
    # Полезно для статистики
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    
    nickname: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nickname_changes: Mapped[int] = mapped_column(Integer, default=0)
    last_nickname_change: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(1), nullable=True) # 'M' или 'F'
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    search_gender: Mapped[str] = mapped_column(String(3), default="any") # 'M', 'F' или 'any'

class Report(Base):
    __tablename__ = 'reports'
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    reported_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = 'transactions'
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    amount: Mapped[int] = mapped_column(Integer) # Сумма в звездах
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)