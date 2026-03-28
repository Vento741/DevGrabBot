"""Рассылка уведомлений команде в личку."""
import logging

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.models import TeamMember, TeamRole

logger = logging.getLogger(__name__)


async def broadcast_to_team(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    text: str,
    parse_mode: str = "HTML",
    role_filter: TeamRole | None = None,
) -> int:
    """Отправить сообщение всем активным участникам команды.

    Args:
        bot: Экземпляр Telegram-бота.
        session_factory: Фабрика async-сессий.
        text: Текст сообщения (HTML).
        parse_mode: Режим парсинга (HTML/Markdown).
        role_filter: Если задан, отправлять только участникам с этой ролью.

    Returns:
        Количество успешно отправленных сообщений.
    """
    async with session_factory() as session:
        query = select(TeamMember).where(TeamMember.is_active.is_(True))
        if role_filter:
            query = query.where(TeamMember.role == role_filter)
        result = await session.execute(query)
        members = list(result.scalars().all())

    sent = 0
    for member in members:
        try:
            await bot.send_message(
                chat_id=member.tg_id,
                text=text,
                parse_mode=parse_mode,
            )
            sent += 1
        except Exception:
            logger.warning("Не удалось отправить сообщение %s (tg_id=%s)", member.name, member.tg_id)

    logger.info("Broadcast отправлен %d/%d участникам", sent, len(members))
    return sent
