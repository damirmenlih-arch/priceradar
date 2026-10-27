import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.db.engine import create_engine, create_session_factory
from app.parser.runner import parse_by_ids

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("priceradar.worker")


async def startup(ctx: dict) -> None:
    engine = create_engine()
    ctx["session_factory"] = create_session_factory(engine)
    ctx["engine"] = engine
    logger.info("worker startup: engine created")


async def shutdown(ctx: dict) -> None:
    engine = ctx.pop("engine", None)
    if engine is not None:
        await engine.dispose()
    logger.info("worker shutdown: engine disposed")


async def parse_all_skus(ctx: dict) -> dict[str, int]:
    """Полный ночной обход всех активных SKU."""
    run = await parse_by_ids(ctx["session_factory"])
    logger.info("parse_all_skus finished: stats=%s", run.stats)
    return run.stats


async def parse_hot_skus(ctx: dict) -> dict[str, int]:
    """Выборочный обход «горячих» SKU каждые 15 минут."""
    run = await parse_by_ids(ctx["session_factory"], hot_only=True)
    logger.info("parse_hot_skus finished: stats=%s", run.stats)
    return run.stats


class WorkerSettings:
    """Конфигурация arq-воркера (запуск: arq app.worker.WorkerSettings)."""

    functions = [parse_all_skus, parse_hot_skus]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_dsn)

    max_jobs = 2
    job_timeout = 1800  # полный обход не должен умереть по таймауту arq
    max_tries = 1  # ретраи сети внутри парсера; на уровне очереди — не нужны

    # Полный обход в 03:00 UTC; «горячие» — каждые 15 минут, без пересечения запусков
    cron_jobs = [
        cron(parse_all_skus, hour=3, minute=0, unique=True),
        cron(parse_hot_skus, minute=set(range(0, 60, 15)), unique=True),
    ]
