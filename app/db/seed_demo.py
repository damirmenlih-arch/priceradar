"""Демо-данные: 3 SKU с реального сайта (books.toscrape.com) + 3 симулированных.

Запуск: python -m app.db.seed_demo
(после python -m app.db.init_db)
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.engine import create_engine, create_session_factory
from app.db.models import SKU

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("priceradar.seed_demo")

DEMO_SKUS: list[dict[str, object]] = [
    # Реальные страницы books.toscrape.com — настоящий HTTP-парсинг
    {
        "internal_sku": "1001001",
        "title": "A Light in the Attic (books.toscrape.com)",
        "url": "http://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        "source": "books_toscrape",
        "is_hot": True,
    },
    {
        "internal_sku": "1001002",
        "title": "Tipping the Velvet (books.toscrape.com)",
        "url": "http://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html",
        "source": "books_toscrape",
        "is_hot": False,
    },
    {
        "internal_sku": "1001003",
        "title": "Soumission (books.toscrape.com)",
        "url": "http://books.toscrape.com/catalogue/soumission_998/index.html",
        "source": "books_toscrape",
        "is_hot": False,
    },
    # Симуляция: «распродажи» и блуждание цены без сети
    {
        "internal_sku": "2001001",
        "title": "Демо-товар «Smart Watch X2» (симуляция)",
        "url": "",
        "source": "simulated",
        "is_hot": True,
    },
    {
        "internal_sku": "2001002",
        "title": "Демо-товар «Наушники AirBeat» (симуляция)",
        "url": "",
        "source": "simulated",
        "is_hot": False,
    },
    {
        "internal_sku": "2001003",
        "title": "Демо-товар «Робот-пылесос CleanBot» (симуляция)",
        "url": "",
        "source": "simulated",
        "is_hot": False,
    },
]


async def seed(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        async with session.begin():
            for item in DEMO_SKUS:
                # Idempotent: INSERT ... ON CONFLICT (internal_sku) DO NOTHING (PostgreSQL),
                # для SQLite — проверка вручную.
                dsn_snippet = str(session.bind.url) if session.bind else ""
                if dsn_snippet.startswith("postgresql"):
                    stmt = pg_insert(SKU).values(**item).on_conflict_do_nothing(
                        index_elements=["internal_sku"]
                    )
                    await session.execute(stmt)
                else:
                    exists = await session.scalar(
                        select(SKU.id).where(SKU.internal_sku == item["internal_sku"])  # type: ignore[arg-type]
                    )
                    if exists is None:
                        session.add(SKU(**item))  # type: ignore[arg-type]
    logger.info("seed done: %d demo SKUs ensured", len(DEMO_SKUS))


async def main() -> None:
    engine = create_engine()
    session_factory = create_session_factory(engine)
    await seed(session_factory)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
