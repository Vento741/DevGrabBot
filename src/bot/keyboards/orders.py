"""Клавиатуры для заявок в групповом чате."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.utils.platforms import get_order_url


def pm_group_actions_kb(
    order_id: int,
    platform_url: str | None = None,
    has_materials: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура в групповом чате для PM-действий с заявкой.

    Строки:
    [Взять себе] [Анализ]
    [Материалы]  [Открыть]   (материалы только если есть, ссылка только если есть)
    [Скрыть]
    """
    rows = [
        [
            InlineKeyboardButton(text="Взять себе", callback_data=f"pmg:take:{order_id}"),
            InlineKeyboardButton(text="Анализ", callback_data=f"pmg:analysis:{order_id}"),
        ],
    ]
    second_row = []
    if has_materials:
        second_row.append(
            InlineKeyboardButton(text="Материалы", callback_data=f"pmg:materials:{order_id}")
        )
    if platform_url:
        second_row.append(
            InlineKeyboardButton(text="Открыть", url=platform_url)
        )
    if second_row:
        rows.append(second_row)
    rows.append([InlineKeyboardButton(text="Скрыть", callback_data=f"pmg:hide:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pm_group_taken_kb(
    order_id: int,
    taken_by_display: str,
    platform_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура после 'Взять себе' — кнопки снимаются, остаётся инфо + ссылка."""
    rows = [
        [InlineKeyboardButton(text=f"В работе: {taken_by_display}", callback_data=f"pmg:info:{order_id}")]
    ]
    if platform_url:
        rows.append([InlineKeyboardButton(text="Открыть на платформе", url=platform_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pm_group_sent_kb(
    order_id: int,
    platform_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура после статуса 'Отправлено клиенту'."""
    rows = [
        [InlineKeyboardButton(text="✅ Отправлено клиенту", callback_data=f"pmg:info:{order_id}")]
    ]
    if platform_url:
        rows.append([InlineKeyboardButton(text="Открыть на платформе", url=platform_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pm_group_cancelled_kb(
    order_id: int,
    platform_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура после отмены/архивации заявки."""
    rows = [
        [InlineKeyboardButton(text="❌ Отменена", callback_data=f"pmg:info:{order_id}")]
    ]
    if platform_url:
        rows.append([InlineKeyboardButton(text="Открыть на платформе", url=platform_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
