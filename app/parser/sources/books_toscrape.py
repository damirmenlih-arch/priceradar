"""Реальный парсер books.toscrape.com — песочница для обучения скрейпингу.

Разметка стабильная и дружелюбная: цена в <p class="price_color"> (£),
наличие в <p class="instock availability">. Никакого anti-bot — идеально,
чтобы пет-проект показывал настоящие HTTP-запросы и BeautifulSoup.
"""

from decimal import Decimal

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.db.models import SKU
from app.parser.http_client import (
    ParsedPrice,
    ParserError,
    fetch_page,
    parse_money,
)


def _extract_books_price(html: str) -> ParsedPrice:
    """Извлечение цены из разметки books.toscrape.com."""
    import datetime as dt

    soup = BeautifulSoup(html, "lxml")

    node = soup.select_one("p.price_color")
    if node is None:
        raise ParserError("p.price_color not found")

    # "£51.77" -> "51.77"
    price = parse_money(node.get_text(strip=True).replace("£", ""))
    if price is None or price <= 0:
        raise ParserError("price not parseable")

    in_stock = soup.select_one("p.instock.availability") is not None

    return ParsedPrice(
        price=price,
        old_price=None,  # у books.toscrape нет зачёркнутых цен
        in_stock=in_stock,
        collected_at=dt.datetime.now(dt.UTC),
    )


async def fetch_price(
    client: httpx.AsyncClient,
    sku: SKU,
    last_price: Decimal | None,
) -> ParsedPrice:
    """Публичный интерфейс источника: та же сигнатура, что у simulated."""
    settings = get_settings()
    return await fetch_page(
        client,
        sku.url,
        extractor=_extract_books_price,
        max_retries=settings.parse_max_retries,
        backoff_base=settings.parse_backoff_base_s,
        backoff_max=settings.parse_backoff_max_s,
        timeout=settings.parse_timeout_s,
    )
