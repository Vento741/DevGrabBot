"""Клавиатуры панели разработчика."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def cancel_dev_kb(back_to: str = "dev:back") -> InlineKeyboardMarkup:
    """Кнопка «Отменить» для FSM-состояний панели разработчика."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить", callback_data=back_to)],
        ],
    )


def dev_main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Главное меню панели разработчика.

    Админ видит все разделы, обычный dev — только свой стек, заявки, статистику.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="Мой стек", callback_data="dev:stack"),
            InlineKeyboardButton(text="Мои заявки", callback_data="dev:orders"),
        ],
        [
            InlineKeyboardButton(text="Платформы", callback_data="dev:platforms"),
            InlineKeyboardButton(text="Команда", callback_data="dev:team"),
        ],
        [
            InlineKeyboardButton(text="Статистика", callback_data="dev:stats"),
        ],
    ]

    if is_admin:
        rows.insert(2, [
            InlineKeyboardButton(text="Стоп-слова", callback_data="dev:stopwords"),
            InlineKeyboardButton(text="Промпты", callback_data="dev:prompts"),
        ])
        rows.insert(3, [
            InlineKeyboardButton(text="Настройки", callback_data="dev:settings"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def platforms_kb(user_platforms: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора платформ для подписки (чекбокс-стиль).

    Каждая платформа — отдельная переключаемая кнопка.
    """
    from src.bot.utils.platforms import ALL_PLATFORMS, get_platform_label

    enabled = {p.lower() for p in (user_platforms or [])}
    rows: list[list[InlineKeyboardButton]] = []
    for platform in ALL_PLATFORMS:
        mark = "✅" if platform in enabled else "☐"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {get_platform_label(platform)}",
                callback_data=f"platforms:toggle:{platform}",
            ),
        ])
    rows.append([InlineKeyboardButton(text="<- Назад", callback_data="dev:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stack_actions_kb() -> InlineKeyboardMarkup:
    """Действия с личным стеком разработчика."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Редактировать primary",
                    callback_data="stack:edit_primary",
                ),
                InlineKeyboardButton(
                    text="Редактировать secondary",
                    callback_data="stack:edit_secondary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Очистить стек",
                    callback_data="stack:clear",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="dev:back"),
            ],
        ],
    )


_SW_PAGE_SIZE = 12


def stop_words_kb(words: list[str], page: int = 0) -> InlineKeyboardMarkup:
    """Список стоп-слов с кнопками удаления, пагинацией, экспортом и очисткой.

    Стоп-слова отображаются по 2 в ряд. Показывается страница page (0-based)
    по _SW_PAGE_SIZE слов. Под словами — ряд навигации по страницам,
    затем кнопки «Добавить», «Экспорт», «Очистить всё» и «Назад».

    callback_data:
      - удаление: sw:del:{word}
      - пагинация: sw:page:{n}
      - добавление: sw:add
      - экспорт: sw:export
      - очистить: sw:clear:confirm
    """
    rows: list[list[InlineKeyboardButton]] = []

    total = len(words)
    total_pages = max(1, (total + _SW_PAGE_SIZE - 1) // _SW_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * _SW_PAGE_SIZE
    page_words = words[start: start + _SW_PAGE_SIZE]

    # Слова по 2 в ряд
    for i in range(0, len(page_words), 2):
        row = [
            InlineKeyboardButton(
                text=f"X {page_words[i]}",
                callback_data=f"sw:del:{page_words[i]}",
            )
        ]
        if i + 1 < len(page_words):
            row.append(
                InlineKeyboardButton(
                    text=f"X {page_words[i + 1]}",
                    callback_data=f"sw:del:{page_words[i + 1]}",
                )
            )
        rows.append(row)

    # Ряд пагинации (скрываем крайние стрелки)
    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"sw:page:{page - 1}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                text=f"Стр. {page + 1}/{total_pages}",
                callback_data="sw:noop",
            )
        )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"sw:page:{page + 1}",
                )
            )
        rows.append(nav_row)

    # Кнопки действий
    rows.append(
        [
            InlineKeyboardButton(
                text="Добавить стоп-слово",
                callback_data="sw:add",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="📥 Экспорт",
                callback_data="sw:export",
            ),
            InlineKeyboardButton(
                text="🗑 Очистить всё",
                callback_data="sw:clear:confirm",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="<- Назад", callback_data="dev:back"),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def stop_words_clear_confirm_kb(count: int) -> InlineKeyboardMarkup:
    """Подтверждение полной очистки списка стоп-слов."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"❌ Да, удалить все {count}",
                    callback_data="sw:clear:do",
                ),
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data="dev:stopwords",
                ),
            ],
        ],
    )


def prompts_list_kb() -> InlineKeyboardMarkup:
    """Список доступных промптов для редактирования."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Анализ заявки",
                    callback_data="prompt:analyze",
                ),
                InlineKeyboardButton(
                    text="Генерация отклика",
                    callback_data="prompt:response",
                ),
                InlineKeyboardButton(
                    text="Pre Roadmap",
                    callback_data="prompt:roadmap",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="dev:back"),
            ],
        ],
    )


def prompt_actions_kb(prompt_key: str) -> InlineKeyboardMarkup:
    """Действия с выбранным промптом: редактировать или сбросить."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Редактировать",
                    callback_data=f"prompt:edit:{prompt_key}",
                ),
                InlineKeyboardButton(
                    text="Сбросить к дефолту",
                    callback_data=f"prompt:reset:{prompt_key}",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="dev:prompts"),
            ],
        ],
    )


def team_list_kb(
    members: list,
    show_add: bool = True,
) -> InlineKeyboardMarkup:
    """Список участников команды.

    Каждый участник — отдельная строка.
    Формат кнопки: {name} ({role.value}) [V/X]
    callback_data: team:member:{id}
    """
    rows: list[list[InlineKeyboardButton]] = []

    for member in members:
        status_icon = "V" if member.is_active else "X"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{member.name} ({member.role.value}) {status_icon}",
                    callback_data=f"team:member:{member.id}",
                )
            ]
        )

    if show_add:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Добавить участника",
                    callback_data="team:add",
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="<- Назад", callback_data="dev:back"),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def member_actions_kb(member_id: int, is_active: bool, show_stack: bool = True) -> InlineKeyboardMarkup:
    """Действия с участником команды: переключить активность, изменить стек."""
    toggle_text = "Деактивировать" if is_active else "Активировать"
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=toggle_text, callback_data=f"team:toggle:{member_id}")],
    ]
    if show_stack:
        rows.append([
            InlineKeyboardButton(text="Primary стек", callback_data=f"team:stack_primary:{member_id}"),
            InlineKeyboardButton(text="Secondary стек", callback_data=f"team:stack_secondary:{member_id}"),
        ])
    rows.append([InlineKeyboardButton(text="<- Назад", callback_data="dev:team")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def role_select_kb() -> InlineKeyboardMarkup:
    """Выбор роли при добавлении нового участника команды."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Developer",
                    callback_data="role:developer",
                ),
                InlineKeyboardButton(
                    text="Manager",
                    callback_data="role:manager",
                ),
            ],
            [
                InlineKeyboardButton(text="Отмена", callback_data="dev:team"),
            ],
        ],
    )


def settings_kb(current_settings: dict) -> InlineKeyboardMarkup:
    """Настройки бота с текущими значениями в тексте кнопок.

    Ожидаемые ключи current_settings:
      - openrouter_model: str
      - parse_interval_sec: int
      - time_threshold_hours: int
    """
    model_val = current_settings.get("openrouter_model", "—")
    # Обрезаем длинное название модели для читаемости в кнопке
    if len(model_val) > 24:
        model_val = model_val[:21] + "..."

    interval_val = current_settings.get("parse_interval_sec", "—")
    threshold_val = current_settings.get("time_threshold_hours", "—")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"AI-модель: {model_val}",
                    callback_data="set:openrouter_model",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Интервал парсинга: {interval_val} сек",
                    callback_data="set:parse_interval_sec",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Порог времени: {threshold_val} ч",
                    callback_data="set:time_threshold_hours",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Час статистики: {current_settings.get('stats_broadcast_hour', '9')} UTC",
                    callback_data="set:stats_broadcast_hour",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Уведомления о заявках: {current_settings.get('notify_label', 'ВКЛ')}",
                    callback_data="dev:toggle_notify",
                ),
            ],
            [
                InlineKeyboardButton(text="<- Назад", callback_data="dev:back"),
            ],
        ],
    )


def _orders_nav_rows(active: str = "") -> list[list[InlineKeyboardButton]]:
    """Навигационные кнопки фильтров заявок с выделением активного таба."""
    _TABS = [
        ("В работе", "in_progress", "orders:in_progress"),
        ("Отправленные", "sent", "orders:sent"),
        ("Архив", "cancelled", "orders:cancelled"),
        ("Все", "all", "orders:all"),
    ]
    tab_row = []
    for label, key, cb in _TABS:
        text = f"• {label}" if key == active else label
        tab_row.append(InlineKeyboardButton(text=text, callback_data=cb))
    return [
        tab_row,
        [InlineKeyboardButton(text="<- Назад", callback_data="dev:back")],
    ]


def orders_filter_kb() -> InlineKeyboardMarkup:
    """Фильтр заявок разработчика (начальный экран)."""
    return InlineKeyboardMarkup(inline_keyboard=_orders_nav_rows())


_STATUS_ICONS: dict[str, str] = {
    "sent": "\u2705",
    "in_progress": "\U0001f528",
    "cancelled": "\u274c",
    "approved": "\U0001f4cb",
    "pending": "\u23f3",
    "editing": "\u270f\ufe0f",
    "rejected": "\U0001f6ab",
    "reassigned": "\U0001f504",
}


def orders_list_kb(
    assignments: list,  # list[OrderAssignment]
    active_filter: str = "",
) -> InlineKeyboardMarkup:
    """Список заявок как кликабельные кнопки -> детальная карточка."""
    rows: list[list[InlineKeyboardButton]] = []

    from src.bot.utils.platforms import get_platform_badge

    for a in assignments:
        order = a.order
        icon = _STATUS_ICONS.get(a.status.value, "")
        badge = get_platform_badge(getattr(order, "platform", None))
        title_short = (order.title[:28] + "...") if len(order.title or "") > 28 else (order.title or "")
        btn_text = f"{icon} {badge} {order.external_id} — {title_short}".strip()
        rows.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"dev_order:{a.id}",
        )])

    if not assignments:
        rows.append([InlineKeyboardButton(
            text="Заявок нет",
            callback_data="noop:empty",
        )])

    # Навигация с выделением активного таба
    rows.extend(_orders_nav_rows(active_filter))

    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_detail_kb(
    order_id: int,
    external_id: str,
    has_materials: bool = False,
    platform=None,
) -> InlineKeyboardMarkup:
    """Кнопки для детальной карточки заявки в 'Мои заявки'."""
    from src.bot.utils.platforms import get_order_url, get_platform_name

    rows: list[list[InlineKeyboardButton]] = []
    if has_materials:
        rows.append([
            InlineKeyboardButton(
                text="\U0001f50d Просмотр материалов",
                callback_data=f"materials:{order_id}",
                style="primary",
            ),
        ])

    platform_name = get_platform_name(platform or "profiru")
    link_url = get_order_url(platform or "profiru", external_id) or (
        f"https://profi.ru/backoffice/n.php?o={external_id}"
    )

    rows.extend([
        [
            InlineKeyboardButton(
                text="Оригинал",
                callback_data=f"original:{order_id}",
            ),
            InlineKeyboardButton(
                text=f"Открыть на {platform_name}",
                url=link_url,
            ),
        ],
        [
            InlineKeyboardButton(
                text="<- Назад к заявкам",
                callback_data="dev:orders",
            ),
        ],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_dev_kb() -> InlineKeyboardMarkup:
    """Простая кнопка возврата в главное меню панели разработчика."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="<- Назад", callback_data="dev:back"),
            ],
        ],
    )
