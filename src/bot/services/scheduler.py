"""Планировщик: ежедневная рассылка статистики + автоархивация."""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.config import Settings
from src.core.database import create_engine, create_session_factory
from src.core.models import AssignmentStatus, OrderAssignment
from src.core.settings_service import get_config_setting
from src.bot.services.analytics import get_daily_broadcast_text
from src.bot.services.broadcast import broadcast_to_team

logger = logging.getLogger(__name__)

AUTO_ARCHIVE_DAYS = 5


async def _seconds_until_next_broadcast(hour: int) -> float:
    """Вычислить секунды до следующего запланированного времени (UTC)."""
    now = datetime.utcnow()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _auto_archive_stale_assignments(
    session_factory: async_sessionmaker, bot: Bot,
) -> int:
    """Автоматически отменять назначения в статусе approved старше 5 дней."""
    threshold = datetime.utcnow() - timedelta(days=AUTO_ARCHIVE_DAYS)
    archived_count = 0

    async with session_factory() as session:
        result = await session.execute(
            select(OrderAssignment)
            .options(
                selectinload(OrderAssignment.order),
                selectinload(OrderAssignment.developer),
            )
            .where(
                OrderAssignment.status == AssignmentStatus.approved,
                OrderAssignment.approved_at.isnot(None),
                OrderAssignment.approved_at < threshold,
            )
        )
        stale = list(result.scalars().all())

        for assignment in stale:
            assignment.status = AssignmentStatus.cancelled
            assignment.cancelled_at = datetime.utcnow()
            archived_count += 1

            # Уведомляем разработчика
            dev = assignment.developer
            if dev and dev.is_active:
                try:
                    await bot.send_message(
                        chat_id=dev.tg_id,
                        text=(
                            f"Заявка <b>{assignment.order.external_id}</b> "
                            f"автоматически архивирована "
                            f"(заказчик не отреагировал на отклик в течение {AUTO_ARCHIVE_DAYS} дней)"
                        ),
                    )
                except Exception:
                    logger.warning(
                        "Не удалось уведомить dev tg_id=%s об автоархивации",
                        dev.tg_id,
                    )

        if stale:
            await session.commit()

    return archived_count


async def run_scheduler_worker(settings: Settings) -> None:
    """Бесконечный цикл: ежедневная рассылка статистики + автоархивация."""
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    logger.info("Scheduler worker запущен")

    try:
        while True:
            # Читаем час рассылки из настроек (может быть изменён через панель)
            async with session_factory() as session:
                hour_str = await get_config_setting(
                    session, "stats_broadcast_hour", settings
                )
            broadcast_hour = int(hour_str)

            wait_sec = await _seconds_until_next_broadcast(broadcast_hour)
            logger.info(
                "Следующая рассылка статистики через %.0f сек. (час=%d UTC)",
                wait_sec, broadcast_hour,
            )
            await asyncio.sleep(wait_sec)

            try:
                async with session_factory() as session:
                    text = await get_daily_broadcast_text(session)

                if text:
                    await broadcast_to_team(bot, session_factory, text)
                    logger.info("Ежедневная статистика отправлена команде")
                else:
                    logger.info("За сегодня активности нет — статистика не отправлена")
            except Exception:
                logger.exception("Ошибка при рассылке статистики")

            # Автоархивация просроченных назначений
            try:
                archived = await _auto_archive_stale_assignments(session_factory, bot)
                if archived:
                    logger.info("Автоархивировано %d просроченных назначений", archived)
            except Exception:
                logger.exception("Ошибка при автоархивации назначений")

            # Защита от повторной отправки при быстром рестарте
            await asyncio.sleep(60)

    finally:
        await bot.session.close()
        await engine.dispose()
