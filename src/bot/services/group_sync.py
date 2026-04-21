"""Синхронизация статуса заявки в групповом чате."""
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def sync_group_message(
    bot: Bot,
    group_chat_id: int | None,
    group_message_id: int | None,
    *,
    new_keyboard: InlineKeyboardMarkup | None = None,
    remove_keyboard: bool = False,
) -> None:
    """Обновить клавиатуру карточки заявки в групповом чате.

    Args:
        bot: Экземпляр Telegram-бота.
        group_chat_id: ID группового чата.
        group_message_id: ID сообщения в группе.
        new_keyboard: Новая клавиатура (если None и remove_keyboard=False — ничего не делаем).
        remove_keyboard: Если True — снять инлайн-кнопки полностью.
    """
    if not group_chat_id or not group_message_id:
        return

    try:
        if new_keyboard is not None:
            await bot.edit_message_reply_markup(
                chat_id=group_chat_id,
                message_id=group_message_id,
                reply_markup=new_keyboard,
            )
        elif remove_keyboard:
            await bot.edit_message_reply_markup(
                chat_id=group_chat_id,
                message_id=group_message_id,
                reply_markup=None,
            )
    except TelegramAPIError as exc:
        logger.warning(
            "Не смог обновить групповое сообщение msg_id=%s: %s",
            group_message_id, exc,
        )
