"""Клавиатуры панели менеджера."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def cancel_mgr_kb(back_to: str = "mgr:back") -> InlineKeyboardMarkup:
    """Кнопка «Отменить» для FSM-состояний панели менеджера."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить", callback_data=back_to)],
        ],
    )


def manager_main_menu_kb(is_paused: bool = False) -> InlineKeyboardMarkup:
    """Главное меню панели менеджера.

    callback_data: mgr:toggle_available, mgr:responses, mgr:style,
                   mgr:profile, mgr:orders, mgr:devs, mgr:analytics
    """
    if is_paused:
        toggle_btn = InlineKeyboardButton(
            text="\u23f8 Приостановлен — нажмите, чтобы запустить",
            callback_data="mgr:toggle_available",
            style="danger",
        )
    else:
        toggle_btn = InlineKeyboardButton(
            text="\u25b6\ufe0f Запущен — нажмите, чтобы остановить",
            callback_data="mgr:toggle_available",
            style="success",
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [toggle_btn],
            [
                InlineKeyboardButton(
                    text="Входящие отклики",
                    callback_data="mgr:responses",
                ),
                InlineKeyboardButton(
                    text="Стиль откликов",
                    callback_data="mgr:style",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Профиль",
                    callback_data="mgr:profile",
                ),
                InlineKeyboardButton(
                    text="Заявки",
                    callback_data="mgr:orders",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Разработчики",
                    callback_data="mgr:devs",
                ),
                InlineKeyboardButton(
                    text="Аналитика",
                    callback_data="mgr:analytics",
                ),
            ],
        ],
    )


def responses_filter_kb() -> InlineKeyboardMarkup:
    """Фильтр откликов менеджера.

    callback_data: resp:new, resp:sent, resp:all, mgr:back
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Новые (ожидают)",
                    callback_data="resp:new",
                ),
                InlineKeyboardButton(
                    text="Отправленные",
                    callback_data="resp:sent",
                ),
                InlineKeyboardButton(
                    text="Все",
                    callback_data="resp:all",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="mgr:back"),
            ],
        ],
    )


def response_actions_kb(response_id: int, assignment_id: int) -> InlineKeyboardMarkup:
    """Действия с конкретным откликом.

    callback_data:
      resp:edit:{response_id}      — редактировать текст отклика
      resp:regen:{assignment_id}   — перегенерировать через AI
      resp:mark_sent:{response_id} — отметить как отправленный
      copy_response:{response_id}  — скопировать текст
      mgr:responses                — назад к списку откликов
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправлено клиенту",
                    callback_data=f"resp:mark_sent:{response_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Редактировать",
                    callback_data=f"resp:edit:{response_id}",
                ),
                InlineKeyboardButton(
                    text="Перегенерировать",
                    callback_data=f"resp:regen:{assignment_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Скопировать",
                    callback_data=f"copy_response:{response_id}",
                ),
                InlineKeyboardButton(
                    text="<- Назад",
                    callback_data="mgr:responses",
                ),
            ],
        ],
    )


def style_settings_kb() -> InlineKeyboardMarkup:
    """Настройки стиля откликов.

    callback_data: style:tone, style:intro, style:rules, mgr:back
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Тон общения",
                    callback_data="style:tone",
                ),
                InlineKeyboardButton(
                    text="Вступление команды",
                    callback_data="style:intro",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Правила генерации",
                    callback_data="style:rules",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="mgr:back"),
            ],
        ],
    )


def profile_settings_kb() -> InlineKeyboardMarkup:
    """Настройки профиля менеджера.

    callback_data: profile:name, profile:signature, profile:contacts, mgr:back
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Имя для откликов",
                    callback_data="profile:name",
                ),
                InlineKeyboardButton(
                    text="Подпись",
                    callback_data="profile:signature",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Контактные данные",
                    callback_data="profile:contacts",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="mgr:back"),
            ],
        ],
    )


def orders_status_kb() -> InlineKeyboardMarkup:
    """Заявки по статусу для панели менеджера.

    callback_data: morders:new, morders:assigned, morders:completed,
                   morders:all, morders:search, mgr:back
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Новые",
                    callback_data="morders:new",
                ),
                InlineKeyboardButton(
                    text="В работе",
                    callback_data="morders:assigned",
                ),
                InlineKeyboardButton(
                    text="Завершённые",
                    callback_data="morders:completed",
                ),
                InlineKeyboardButton(
                    text="Все",
                    callback_data="morders:all",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Поиск по ID",
                    callback_data="morders:search",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="mgr:back"),
            ],
        ],
    )


def developers_list_kb(developers: list) -> InlineKeyboardMarkup:
    """Список разработчиков для панели менеджера.

    Каждый разработчик — отдельная строка.
    Формат кнопки: {name} (@{username}) или {name} если нет username.
    callback_data: mdev:{id}, mgr:back
    """
    rows: list[list[InlineKeyboardButton]] = []

    for dev in developers:
        if dev.tg_username:
            label = f"{dev.name} (@{dev.tg_username})"
        else:
            label = dev.name
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"mdev:{dev.id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="<- Назад", callback_data="mgr:back"),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def developer_detail_kb(developer_id: int) -> InlineKeyboardMarkup:
    """Детали разработчика: назначить на заявку или посмотреть историю.

    callback_data: mdev:assign:{developer_id}, mdev:history:{developer_id}, mgr:devs
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Назначить на заявку",
                    callback_data=f"mdev:assign:{developer_id}",
                ),
                InlineKeyboardButton(
                    text="История заказов",
                    callback_data=f"mdev:history:{developer_id}",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="mgr:devs"),
            ],
        ],
    )


def back_to_manager_kb() -> InlineKeyboardMarkup:
    """Простая кнопка возврата в главное меню панели менеджера."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="<- Назад", callback_data="mgr:back"),
            ],
        ],
    )
