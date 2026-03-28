"""Единая точка входа: запускает бота, AI-воркер и notification-воркер."""
import asyncio
import logging

from src.core.config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("devgrabbot")


async def main():
    settings = Settings()

    from src.bot.bot import run_bot
    from src.ai.worker import run_ai_worker
    from src.bot.services.notification import run_notification_worker
    from src.bot.services.scheduler import run_scheduler_worker

    logger.info("DevGrabBot запускается...")

    tasks = [
        asyncio.create_task(run_bot(settings), name="bot"),
        asyncio.create_task(run_ai_worker(settings), name="ai_worker"),
        asyncio.create_task(run_notification_worker(settings), name="notification_worker"),
        asyncio.create_task(run_scheduler_worker(settings), name="scheduler"),
    ]

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if task.exception():
                logger.error(f"Задача {task.get_name()} завершилась с ошибкой: {task.exception()}")
        for task in pending:
            task.cancel()
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки")
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
