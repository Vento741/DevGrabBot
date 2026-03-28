"""Точка входа Telegram-бота DevGrabBot (aiogram 3)."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from src.core.config import Settings
from src.core.database import create_engine, create_session_factory
from src.core.redis import RedisClient
from src.bot.handlers import start, orders, review, manager, dev_panel, manager_panel
from src.bot.middlewares.auth import AuthMiddleware

logger = logging.getLogger(__name__)


def _create_bot(settings: Settings) -> Bot:
    """Создать экземпляр Bot с настройками по умолчанию."""
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _create_dispatcher(settings: Settings, session_factory) -> Dispatcher:
    """Создать Dispatcher, подключить роутеры и middleware."""
    dp = Dispatcher(storage=MemoryStorage())

    # Передаём зависимости через workflow_data
    dp["settings"] = settings
    dp["session_factory"] = session_factory
    dp["redis_client"] = RedisClient(settings)

    # Подключаем middleware авторизации
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())

    # Подключаем роутеры
    dp.include_router(start.router)
    dp.include_router(orders.router)
    dp.include_router(review.router)
    dp.include_router(manager.router)
    dp.include_router(dev_panel.router)
    dp.include_router(manager_panel.router)

    return dp


async def run_bot(settings: Settings) -> None:
    """Запустить Telegram-бота.

    Инициализирует БД-подключение, создаёт бота и диспетчер,
    запускает polling.

    Args:
        settings: Конфигурация приложения.
    """
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    bot = _create_bot(settings)
    dp = _create_dispatcher(settings, session_factory)

    logger.info("DevGrabBot запускается...")

    try:
        await dp.start_polling(bot)
    finally:
        await dp["redis_client"].close()
        await engine.dispose()
        await bot.session.close()
        logger.info("DevGrabBot остановлен.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    _settings = Settings()
    asyncio.run(run_bot(_settings))
