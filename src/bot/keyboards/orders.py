"""Клавиатуры для заявок в групповом чате."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.utils.platforms import get_order_url


def order_actions_kb(order_id: int) -> InlineKeyboardMarkup:
    """Клавиатура с действиями для новой заявки в группе."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u2705 Взять",
                    callback_data=f"take:{order_id}",
                ),
                InlineKeyboardButton(
                    text="\u274c Пропустить",
                    callback_data=f"skip:{order_id}",
                ),
            ],
        ],
    )


def order_taken_kb(
    order_id: int,
    external_id: str,
    developer_username: str,
    platform: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура после взятия заявки: красная кнопка 'Взято' + ссылки."""
    link_url = get_order_url(platform or "profiru", external_id) or (
        f"https://profi.ru/backoffice/n.php?o={external_id}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Взято \u2014 {developer_username}",
                    callback_data=f"taken_info:{order_id}",
                    style="danger",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Ссылка",
                    url=link_url,
                ),
                InlineKeyboardButton(
                    text="Оригинал",
                    callback_data=f"original:{order_id}",
                ),
            ],
        ],
    )
