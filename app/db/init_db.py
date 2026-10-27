"""Инициализация схемы БД: create_all + частичный индекс против дублей.

Запуск: python -m app.db.init_db
"""

import asyncio
import logging

from sqlalchemy import text

from app.db.engine import create_engine
from app.db.models import Base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("priceradar.init_db")

PARTIAL_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_history_unchanged
ON price_history (sku_id, price)
WHERE in_stock;
"""


async def init() -> None:
    engine = create_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(PARTIAL_UNIQUE_INDEX))
        logger.info("schema created / verified")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init())
