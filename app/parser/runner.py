import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db.models import PriceHistory, SKU
from app.parser.http_client import ParserError, RetriableError, create_client
from app.parser.sources.registry import get_parser

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParseRunResult:
    """Итог одного прогона парсера: счётчики статусов и список SKU с изменением цены."""

    stats: dict[str, int] = field(
        default_factory=lambda: {
            "updated": 0,
            "unchanged": 0,
            "failed_retries": 0,
            "failed_parse": 0,
        }
    )
    changed_sku_ids: list[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.stats.values())


async def process_one_sku(
    session_factory: async_sessionmaker,
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    sku: SKU,
) -> tuple[str, int | None]:
    """Обработка одного SKU: last price -> fetch (по источнику) -> compare -> persist.

    Возвращает (статус, sku_id), где sku_id не None только при изменении цены.
    """
    settings = get_settings()

    async with session_factory() as session:
        last_price: Decimal | None = await session.scalar(
            select(PriceHistory.price)
            .where(PriceHistory.sku_id == sku.id)
            .order_by(PriceHistory.collected_at.desc())
            .limit(1)
        )

    async with sem:
        try:
            parser = get_parser(sku.source)
            parsed = await parser(client, sku, last_price)
        except RetriableError as exc:
            logger.error("SKU %s (%d): retries exhausted: %s", sku.internal_sku, sku.id, exc)
            return "failed_retries", None
        except ParserError as exc:
            logger.error("SKU %s (%d): parse error: %s", sku.internal_sku, sku.id, exc)
            return "failed_parse", None

    if last_price == parsed.price:
        return "unchanged", None

    async with session_factory() as session:
        async with session.begin():
            session.add(
                PriceHistory(
                    sku_id=sku.id,
                    price=parsed.price,
                    old_price=parsed.old_price,
                    in_stock=parsed.in_stock,
                )
            )

    logger.info(
        "SKU %s (%d): price %s -> %s (stock=%s)",
        sku.internal_sku,
        sku.id,
        last_price,
        parsed.price,
        parsed.in_stock,
    )
    return "updated", sku.id


async def parse_by_ids(
    session_factory: async_sessionmaker,
    sku_ids: list[int] | None = None,
    hot_only: bool = False,
) -> ParseRunResult:
    """Обход SKU батчами по parse_batch_size, внутри батча — Semaphore(parse_concurrency).

    Args:
        session_factory: фабрика асинхронных сессий.
        sku_ids: конкретные SKU (для ручного запуска/алертов); None — все активные.
        hot_only: только «горячие» SKU (для ежечасного выборочного обхода).

    Returns:
        ParseRunResult: счётчики статусов + id SKU, у которых изменилась цена.
    """
    settings = get_settings()
    sem = asyncio.Semaphore(settings.parse_concurrency)
    result = ParseRunResult()

    query = select(SKU).where(SKU.is_active.is_(True))
    if hot_only:
        query = query.where(SKU.is_hot.is_(True))
    if sku_ids:
        query = query.where(SKU.id.in_(sku_ids))

    async with session_factory() as session:
        skus = (await session.scalars(query)).all()

    if not skus:
        logger.info("parse run: nothing to process (hot_only=%s)", hot_only)
        return result

    logger.info(
        "parse run: %d SKUs, batch=%d, concurrency=%d",
        len(skus),
        settings.parse_batch_size,
        settings.parse_concurrency,
    )

    async with create_client() as client:
        for offset in range(0, len(skus), settings.parse_batch_size):
            batch = skus[offset : offset + settings.parse_batch_size]
            outcomes = await asyncio.gather(
                *(process_one_sku(session_factory, client, sem, sku) for sku in batch),
                return_exceptions=True,
            )
            for sku, outcome in zip(batch, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    logger.exception("unexpected failure on SKU %d: %r", sku.id, outcome)
                    result.stats["failed_retries"] += 1
                else:
                    status, changed_id = outcome
                    result.stats[status] += 1
                    if changed_id is not None:
                        result.changed_sku_ids.append(changed_id)
            logger.info(
                "batch done: %d/%d SKUs processed",
                min(offset + settings.parse_batch_size, len(skus)),
                len(skus),
            )

    logger.info("parse run finished: stats=%s, changed=%d", result.stats, len(result.changed_sku_ids))
    return result
