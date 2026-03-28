"""Middleware авторизации: проверка принадлежности пользователя к команде."""
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.models import TeamMember, TeamRole

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Проверяет, что пользователь зарегистрирован в team_members.

    Пропускает команду /start без проверки.
    Для остальных сообщений и callback-запросов требует наличие
    пользователя в таблице team_members с is_active=True.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Выполнить проверку авторизации перед вызовом хендлера."""
        # Блокируем команды и callback в группах — бот работает только в ЛС
        if isinstance(event, Message):
            if event.chat.type != "private":
                # В группе бот только отправляет заявки, команды игнорируем
                logger.debug(
                    "Игнорируем сообщение в группе chat_id=%s от user=%s",
                    event.chat.id,
                    event.from_user.id if event.from_user else "?",
                )
                return None
        elif isinstance(event, CallbackQuery):
            if event.message and event.message.chat.type != "private":
                # Пропускаем кнопки заявок в группе (take/skip/taken_info/original)
                cb_data = event.data or ""
                _group_allowed = ("take:", "skip:", "taken_info:", "original:", "materials:")
                if cb_data.startswith(_group_allowed):
                    pass  # разрешаем — это кнопки заявок
                else:
                    logger.debug(
                        "Игнорируем callback в группе chat_id=%s от user=%s",
                        event.message.chat.id,
                        event.from_user.id if event.from_user else "?",
                    )
                    return None

        # Определяем user_id и проверяем, нужна ли авторизация
        user_id: int | None = None

        if isinstance(event, Message):
            # Пропускаем /start
            if event.text and event.text.startswith("/start"):
                return await handler(event, data)
            user_id = event.from_user.id if event.from_user else None

        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None

        if user_id is None:
            return await handler(event, data)

        # Проверяем наличие в БД
        session_factory: async_sessionmaker[AsyncSession] = data["session_factory"]

        async with session_factory() as session:
            result = await session.execute(
                select(TeamMember).where(
                    TeamMember.tg_id == user_id,
                    TeamMember.is_active.is_(True),
                )
            )
            member = result.scalar_one_or_none()

        if not member:
            logger.warning(
                "Неавторизованный доступ: tg_id=%s", user_id,
            )
            if isinstance(event, Message):
                await event.answer(
                    "У вас нет доступа к этому боту. "
                    "Обратитесь к администратору.",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "У вас нет доступа.", show_alert=True,
                )
            return None

        # Проверяем доступ dev к панели менеджера
        if member.role == TeamRole.developer:
            is_manager_area = False
            if isinstance(event, Message) and event.text:
                is_manager_area = event.text.startswith("/manager")
            elif isinstance(event, CallbackQuery) and event.data:
                is_manager_area = event.data.startswith("mgr:")
            if is_manager_area:
                logger.warning(
                    "Dev tg_id=%s попытался открыть панель менеджера", user_id,
                )
                if isinstance(event, Message):
                    await event.answer("Панель менеджера доступна только менеджерам.")
                elif isinstance(event, CallbackQuery):
                    await event.answer(
                        "Панель менеджера доступна только менеджерам.",
                        show_alert=True,
                    )
                return None

        # Передаём member в data для использования в хендлерах
        data["member"] = member

        return await handler(event, data)
