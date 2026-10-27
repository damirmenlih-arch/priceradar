import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """Anti-spam: фиксированное окно в Redis.

    Ключ throttle:{user_id}:{scope} — INCR + EXPIRE NX.
    Сообщения сверх rate возвращают «не спамьте» и НЕ пробрасываются дальше.
    """

    def __init__(self, redis: Redis, scope: str = "default") -> None:
        self._redis = redis
        self._scope = scope

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        settings = get_settings()

        user_id: int | None = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if user_id is None:
            return await handler(event, data)

        key = f"throttle:{user_id}:{self._scope}"
        current = await self._redis.incr(key)
        if current == 1:
            await self._redis.expire(key, settings.throttle_window_s)

        if current > settings.throttle_rate:
            logger.debug(
                "throttled user %d (scope=%s, hits=%d)", user_id, self._scope, current
            )
            if isinstance(event, CallbackQuery):
                await event.answer("Слишком быстро. Подождите пару секунд.", show_alert=False)
            elif isinstance(event, Message):
                await event.answer("⏳ Не спамьте, ответ уже обрабатывается.")
            return None  # хендлер не вызывается

        return await handler(event, data)
