import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.handlers import router as subscriptions_router
from app.bot.middlewares import ThrottlingMiddleware
from app.config import get_settings
from app.db.engine import create_engine, create_session_factory
from app.services.monitor import check_and_notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("priceradar.bot")


async def scheduled_check(session_factory: async_sessionmaker, bot: Bot) -> None:
    """Фоновая задача APScheduler: проверка цен + уведомления без участия пользователя."""
    try:
        run, sent = await check_and_notify(session_factory, bot)
        logger.info(
            "scheduled check done: total=%d, changed=%d, notified=%d",
            run.total,
            len(run.changed_sku_ids),
            sent,
        )
    except Exception:
        logger.exception("scheduled check failed")


async def run() -> None:
    settings = get_settings()

    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен от @BotFather."
        )

    # Redis опционален: без него FSM хранится в памяти и throttling выключается
    redis: Redis | None = (
        Redis.from_url(settings.redis_dsn, decode_responses=True)
        if settings.redis_dsn
        else None
    )
    storage: BaseStorage = RedisStorage(redis=redis) if redis else MemoryStorage()
    if redis:
        logger.info("storage: Redis + throttling enabled")
    else:
        logger.info("storage: in-memory (REDIS_DSN не задан — throttling выключен)")

    engine = create_engine()
    session_factory = create_session_factory(engine)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # session_factory прокидывается в хендлеры через Dispatcher — aiogram 3.x
    # инжектит его в параметры хендлеров автоматически.
    dp = Dispatcher(
        storage=storage,
        session_factory=session_factory,
    )

    if redis:
        dp.message.middleware(ThrottlingMiddleware(redis))
        dp.callback_query.middleware(ThrottlingMiddleware(redis, scope="callbacks"))
    dp.include_router(subscriptions_router)

    # APScheduler: периодическая проверка цен и уведомления подписчикам
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        scheduled_check,
        trigger=IntervalTrigger(minutes=settings.check_interval_minutes),
        args=(session_factory, bot),
        id="hourly_check",
        max_instances=1,   # не пускать второй прогон, пока предыдущий не завершился
        coalesce=True,     # пропущенные тики не копятся
        next_run_time=datetime.now(UTC),  # первый прогон сразу после старта — для демо
    )
    scheduler.start()
    logger.info("scheduler started: check every %d minute(s)", settings.check_interval_minutes)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("starting polling")
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()
        if redis is not None:
            await redis.aclose()


if __name__ == "__main__":
    asyncio.run(run())
