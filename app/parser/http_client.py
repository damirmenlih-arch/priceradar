import asyncio
import datetime as dt
import logging
import random
from dataclasses import dataclass
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings

logger = logging.getLogger(__name__)

USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
)


class ParserError(Exception):
    """Базовая ошибка парсинга (страница без цены, изменённая разметка)."""


class RetriableError(ParserError):
    """Ошибка, которую стоит повторить с backoff (429 / 5xx / сеть)."""


@dataclass(frozen=True, slots=True)
class ParsedPrice:
    price: Decimal
    old_price: Decimal | None
    in_stock: bool
    collected_at: dt.datetime


def build_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }


def parse_money(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    cleaned = raw.replace("\u00a0", "").replace(" ", "").replace("₽", "").strip()
    cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def extract_price(html: str) -> ParsedPrice:
    """Извлечение цены из HTML. Селекторы под конкретный маркетплейс выносятся сюда."""
    soup = BeautifulSoup(html, "lxml")

    node = soup.select_one('[data-widget="webPrice"]')
    if node is None:
        raise ParserError("price widget not found")

    price = parse_money(node.get_text(" ", strip=True))
    if price is None or price <= 0:
        raise ParserError("price not parseable")

    old_node = node.select_one("s[data-widget='webSalePrice']") or node.select_one("s")
    old_price = parse_money(old_node.get_text(strip=True)) if old_node else None

    in_stock = soup.select_one('[data-widget="OutOfStock"]') is None

    return ParsedPrice(
        price=price,
        old_price=old_price if old_price and old_price > price else None,
        in_stock=in_stock,
        collected_at=dt.datetime.now(dt.UTC),
    )


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_retries: int,
    backoff_base: float,
    backoff_max: float,
    timeout: float,
    extractor: Callable[[str], ParsedPrice] = extract_price,
) -> ParsedPrice:
    """GET с экспоненциальным backoff на 429/5xx и сетевые ошибки.

    `extractor` — функция разбора HTML (у каждого источника своя разметка)."""
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        delay = min(backoff_base * (2 ** (attempt - 1)), backoff_max)
        jitter = delay * 0.2

        try:
            response = await client.get(
                url,
                headers=build_headers(),
                timeout=httpx.Timeout(timeout, connect=5.0),
                follow_redirects=True,
            )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            last_exc = exc
            logger.warning("network error (attempt %d/%d) %s: %r", attempt, max_retries, url, exc)
            await asyncio.sleep(delay + random.uniform(0, jitter))
            continue

        if response.status_code == 429:
            # Уважаем Retry-After, если сервер его прислал
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
            last_exc = RetriableError(f"429 rate limited on {url}")
            logger.warning("429 (attempt %d/%d) %s, sleeping %.1fs", attempt, max_retries, url, wait)
            await asyncio.sleep(wait)
            continue

        if response.status_code >= 500:
            last_exc = RetriableError(f"{response.status_code} on {url}")
            logger.warning("%d (attempt %d/%d) %s", response.status_code, attempt, max_retries, url)
            await asyncio.sleep(delay + random.uniform(0, jitter))
            continue

        if response.status_code in (403, 404):
            raise ParserError(f"non-retriable status {response.status_code} for {url}")

        response.raise_for_status()
        return extractor(response.text)

    raise RetriableError(f"exhausted {max_retries} retries for {url}") from last_exc


def create_client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=settings.parse_concurrency + 5),
        transport=httpx.AsyncHTTPTransport(retries=0),  # ретраи свои, httpx-овские отключаем
    )
