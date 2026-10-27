import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SKU(Base):
    """Отслеживаемый товар (наш SKU на маркетплейсе)."""

    __tablename__ = "skus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_sku: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024), default="")
    # Источник данных: "simulated" | "books_toscrape" (см. app/parser/sources/registry.py)
    source: Mapped[str] = mapped_column(String(32), default="simulated", server_default="simulated")
    is_hot: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    price_records: Mapped[list["PriceHistory"]] = relationship(
        back_populates="sku", lazy="noload"
    )
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="sku", lazy="noload"
    )


class PriceHistory(Base):
    """История цен: одна строка = одно измерение одного SKU."""

    __tablename__ = "price_history"
    __table_args__ = (
        Index("ix_price_history_sku_collected", "sku_id", "collected_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("skus.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    collected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sku: Mapped[SKU] = relationship(back_populates="price_records", lazy="joined")


class Subscription(Base):
    """Подписка пользователя бота на SKU."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "sku_id", name="uq_subscription_user_sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    sku_id: Mapped[int] = mapped_column(
        ForeignKey("skus.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sku: Mapped[SKU] = relationship(back_populates="subscriptions", lazy="joined")
