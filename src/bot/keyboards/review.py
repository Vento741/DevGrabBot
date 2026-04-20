"""Клавиатуры для редактирования и утверждения отклика."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.utils.platforms import get_order_url, get_platform_name


def _open_platform_button(
    platform: str | None, external_id: str | None
) -> InlineKeyboardButton | None:
    """Построить кнопку «Открыть на <платформа>» или вернуть None."""
    if not external_id:
        return None
    url = get_order_url(platform or "profiru", external_id)
    if not url:
        return None
    name = get_platform_name(platform or "profiru")
    return InlineKeyboardButton(
        text=f"Открыть на {name}",
        url=url,
        style="primary",
    )


def cancel_review_kb(assignment_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Отменить» для FSM-состояний редактирования отклика."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="Отменить",
                callback_data=f"cancel_review:{assignment_id}",
            )],
        ],
    )


def review_actions_kb(
    assignment_id: int,
    order_id: int | None = None,
    external_id: str | None = None,
    platform: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура редактирования отклика в личке разработчика."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Изменить цену",
                callback_data=f"edit_price:{assignment_id}",
            ),
            InlineKeyboardButton(
                text="Изменить сроки",
                callback_data=f"edit_timeline:{assignment_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Изменить стек",
                callback_data=f"edit_stack:{assignment_id}",
            ),
            InlineKeyboardButton(
                text="Добавить заметку",
                callback_data=f"edit_custom:{assignment_id}",
            ),
        ],
    ]

    if order_id is not None:
        rows.append([
            InlineKeyboardButton(
                text="Pre Roadmap",
                callback_data=f"roadmap:{order_id}",
            ),
            InlineKeyboardButton(
                text="Оригинал",
                callback_data=f"original:{order_id}",
            ),
        ])

    btn = _open_platform_button(platform, external_id)
    if btn is not None:
        rows.append([btn])

    rows.append([
        InlineKeyboardButton(
            text="Редактировать отклик",
            callback_data=f"edit_response:{assignment_id}",
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            text="Утвердить и отправить",
            callback_data=f"approve:{assignment_id}",
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            text="\u274c Отказаться от заявки",
            callback_data=f"reject_order:{assignment_id}",
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def approved_kb(
    external_id: str | None = None,
    manager_tg_username: str | None = None,
    order_id: int | None = None,
    platform: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура после утверждения отклика: статус + связь с PM + оригинал."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Отклик отправлен к PM",
                callback_data="noop:approved",
            ),
        ],
    ]

    if manager_tg_username:
        rows.append([
            InlineKeyboardButton(
                text="Связаться с PM",
                url=f"https://t.me/{manager_tg_username}",
            ),
        ])

    extra_row: list[InlineKeyboardButton] = []
    if order_id is not None:
        extra_row.append(
            InlineKeyboardButton(
                text="Оригинал",
                callback_data=f"original:{order_id}",
            ),
        )
    btn = _open_platform_button(platform, external_id)
    if btn is not None:
        # В этом ряду стиль не нужен — кнопка второстепенная.
        extra_row.append(
            InlineKeyboardButton(text=btn.text, url=btn.url),
        )
    if extra_row:
        rows.append(extra_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def copy_response_kb(
    response_id: int,
    external_id: str | None = None,
    platform: str | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки для менеджера: копировать отклик + ссылка на платформу."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="\U0001f4cb Скопировать отклик",
                callback_data=f"copy_response:{response_id}",
            ),
        ],
    ]

    btn = _open_platform_button(platform, external_id)
    if btn is not None:
        rows.append([btn])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def pm_response_kb(
    assignment_id: int,
    response_id: int,
    external_id: str | None = None,
    hide_sent: bool = False,
    hide_in_progress: bool = False,
    hide_cancel: bool = False,
    platform: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура для PM после утверждения отклика разработчиком."""
    rows: list[list[InlineKeyboardButton]] = []

    if not hide_sent:
        rows.append([InlineKeyboardButton(
            text="\u2705 Отклик отправлен",
            callback_data=f"pm_sent:{assignment_id}",
        )])

    if not hide_in_progress:
        rows.append([InlineKeyboardButton(
            text="\U0001f528 В работе",
            callback_data=f"pm_in_progress:{assignment_id}",
        )])

    if not hide_cancel:
        rows.append([InlineKeyboardButton(
            text="\u274c Отмена (В архив)",
            callback_data=f"pm_cancel:{assignment_id}",
        )])

    rows.append([InlineKeyboardButton(
        text="\U0001f4cb Скопировать отклик",
        callback_data=f"copy_response:{response_id}",
    )])

    btn = _open_platform_button(platform, external_id)
    if btn is not None:
        rows.append([InlineKeyboardButton(text=btn.text, url=btn.url)])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def pm_status_badge_kb(
    status_text: str,
    response_id: int,
    external_id: str | None = None,
    platform: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура-бейдж со статусом (финальное состояние)."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=status_text, callback_data="noop:status")],
        [InlineKeyboardButton(
            text="\U0001f4cb Скопировать отклик",
            callback_data=f"copy_response:{response_id}",
        )],
    ]
    btn = _open_platform_button(platform, external_id)
    if btn is not None:
        rows.append([InlineKeyboardButton(text=btn.text, url=btn.url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
