"""Мониторинг цен + уведомления подписчиков.

Единственная точка, где связываются парсер, БД и Telegram:
  parse_by_ids() -> changed_sku_ids -> подписчики -> bot.send_message().
Вызывается из APScheduler (раз в час) и из команды /check.
"""

import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import PriceHistory, SKU, Subscription
from app.parser.runner import ParseRunResult, parse_by_ids

logger = logging.getLogger(__name__)


async def _load_changes(
    session_factory: async_sessionmaker,
    sku_ids: list[int],
) -> dict[int, dict[str, object]]:
    """Для каждого изменившегося SKU: title, новая цена, предыдущая, наличие.

    Берём последние две записи price_history на SKU (новая = последняя).
    """
    async with session_factory() as session:
        skus = {
            s.id: s
            for s in (await session.scalars(select(SKU).where(SKU.id.in_(sku_ids)))).all()
        }
        rows = (
            await session.scalars(
                select(PriceHistory)
                .where(PriceHistory.sku_id.in_(sku_ids))
                .order_by(PriceHistory.sku_id, PriceHistory.collected_at.desc())
            )
        ).all()

    changes: dict[int, dict[str, object]] = {}
    for row in rows:
        info = changes.setdefault(
            row.sku_id,
            {
                "sku": skus.get(row.sku_id),
                "new_price": None,
                "old_price": None,
                "in_stock": True,
            },
        )
        if info["new_price"] is None:
            info["new_price"] = row.price
            info["in_stock"] = row.in_stock
        elif info["old_price"] is None:
            info["old_price"] = row.price

    return changes


def _build_message(info: dict[str, object]) -> str:
    sku: SKU | None = info["sku"]  # type: ignore[assignment]
    assert sku is not None
    new_price: Decimal = info["new_price"]  # type: ignore[assignment]
    old_price = info["old_price"]

    lines = [f"<b>{sku.title}</b>", f"SKU <code>{sku.internal_sku}</code>"]

    if old_price is None:
        lines.append(f"Первая зафиксированная цена: <b>{new_price:.2f}</b>")
    else:
        old: Decimal = old_price  # type: ignore[assignment]
        if old > 0:
            pct = (new_price - old) / old * 100
            arrow = "📉" if pct < 0 else "📈"
            lines.append(f"{old:.2f} → <b>{new_price:.2f}</b>  {arrow} {pct:+.1f}%")
        else:
            lines.append(f"Новая цена: <b>{new_price:.2f}</b>")

    if not info["in_stock"]:
        lines.append("⚠️ Нет в наличии")

    return "\n".join(lines)


async def check_and_notify(
    session_factory: async_sessionmaker,
    bot: Bot,
) -> tuple[ParseRunResult, int]:
    """Полный цикл: проверка цен -> алерты подписчикам.

    Returns:
        (результат прогона парсера, количество отправленных уведомлений).
    """
    run = await parse_by_ids(session_factory)
    if not run.changed_sku_ids:
        return run, 0

    changes = await _load_changes(session_factory, run.changed_sku_ids)

    async with session_factory() as session:
        targets = (
            await session.execute(
                select(Subscription.user_id, Subscription.sku_id)
                .join(SKU, Subscription.sku_id == SKU.id)
                .where(
                    Subscription.is_active.is_(True),
                    Subscription.sku_id.in_(run.changed_sku_ids),
                )
            )
        ).all()

    sent = 0
    for user_id, sku_id in targets:
        info = changes.get(sku_id)
        if info is None:
            continue
        try:
            await bot.send_message(user_id, _build_message(info))
            sent += 1
        except TelegramForbiddenError:
            # Пользователь заблокировал бота — глушим подписку, чтобы не ронять цикл
            logger.info("user %d blocked the bot; deactivating subscriptions", user_id)
            async with session_factory() as session:
                async with session.begin():
                    subs = (
                        await session.scalars(
                            select(Subscription).where(
                                Subscription.user_id == user_id,
                                Subscription.sku_id == sku_id,
                            )
                        )
                    ).all()
                    for sub in subs:
                        sub.is_active = False
        except Exception:
            logger.exception("failed to notify user %d about SKU %d", user_id, sku_id)

    logger.info("check_and_notify: stats=%s, changed=%d, notified=%d",
                run.stats, len(run.changed_sku_ids), sent)
    return run, sent
