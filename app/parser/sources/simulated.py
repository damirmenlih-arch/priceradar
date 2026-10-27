"""Симулированный источник цен: случайное блуждание + распродажи.

Позволяет демонстрировать полный цикл (парсинг -> изменение -> алерт в Telegram)
без сети, блокировок и anti-bot систем маркетплейсов.
"""

import datetime as dt
import random
from decimal import Decimal

import httpx

from app.db.models import SKU
from app.parser.http_client import ParsedPrice

MIN_PRICE = Decimal("100")
MAX_PRICE = Decimal("20000")


async def fetch_price(
    client: httpx.AsyncClient,  # noqa: ARG001 — интерфейс единый с реальными источниками
    sku: SKU,
    last_price: Decimal | None,
) -> ParsedPrice:
    """Генерация следующей цены.

    Нет истории -> базовая цена берётся случайно.
    Есть история -> случайное блуждание ±3%, с шансом 15% — «распродажа» (-5..-20%).
    """
    rng = random.Random()

    if last_price is None or last_price <= 0:
        base = Decimal(rng.randint(500, 5000))
    else:
        base = last_price

    change_pct = rng.uniform(-0.03, 0.04)
    on_sale = rng.random() < 0.15
    if on_sale:
        change_pct -= rng.uniform(0.05, 0.20)

    new_price = max(MIN_PRICE, min(MAX_PRICE, base * (1 + Decimal(str(change_pct)))))
    new_price = new_price.quantize(Decimal("0.01"))

    old_price = base if on_sale and new_price < base else None

    return ParsedPrice(
        price=new_price,
        old_price=old_price,
        in_stock=rng.random() > 0.05,
        collected_at=dt.datetime.now(dt.UTC),
    )
