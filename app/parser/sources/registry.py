"""Реестр источников цен.

Добавление нового источника:
  1. создать app/parser/sources/<name>.py с функцией
     async def fetch_price(client, sku, last_price) -> ParsedPrice;
  2. вписать её в PARSERS.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

import httpx

from app.db.models import SKU
from app.parser.http_client import ParsedPrice, ParserError
from app.parser.sources import books_toscrape, simulated

PriceFetcher = Callable[[httpx.AsyncClient, SKU, Decimal | None], Awaitable[ParsedPrice]]

PARSERS: dict[str, PriceFetcher] = {
    "simulated": simulated.fetch_price,
    "books_toscrape": books_toscrape.fetch_price,
}


def get_parser(source: str) -> PriceFetcher:
    try:
        return PARSERS[source]
    except KeyError:
        raise ParserError(
            f"unknown source {source!r}; known: {', '.join(PARSERS)}"
        ) from None
