from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Токен Telegram-бота (обязателен для app.bot.main)
    bot_token: str = ""

    # БД: локально по умолчанию SQLite; в docker-compose переопределяется на PostgreSQL
    db_dsn: str = "sqlite+aiosqlite:///./priceradar.db"

    # Redis: пусто -> FSM в памяти (MemoryStorage), без throttling.
    # Заполнено -> RedisStorage + anti-spam middleware.
    redis_dsn: str = ""

    # Периодическая проверка цен APScheduler'ом (в процессе бота)
    check_interval_minutes: int = 60

    # Парсер
    parse_batch_size: int = 50
    parse_concurrency: int = 10
    parse_timeout_s: float = 15.0
    parse_max_retries: int = 5
    parse_backoff_base_s: float = 1.5
    parse_backoff_max_s: float = 60.0

    # Throttling
    throttle_rate: int = 3  # сообщений
    throttle_window_s: int = 2  # за окно


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
