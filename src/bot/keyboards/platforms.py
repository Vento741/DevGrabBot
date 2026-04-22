"""Клавиатуры для двухуровневых настроек платформ (Phase 4 v2.5).

Единый callback-префикс: plat:*
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.core.platform_config import KworkFilters, PlatformConfig

_PREFIX = "plat"

# ---------------------------------------------------------------------------
# Уровень 1: список платформ
# ---------------------------------------------------------------------------


def platforms_list_kb(
    profiru_enabled: bool,
    kwork_enabled: bool,
    *,
    back_cb: str = "mgr:back",
) -> InlineKeyboardMarkup:
    """Список платформ с чекбоксом on/off и кнопкой «Настроить».

    back_cb — callback_data кнопки «← Назад» (зависит от точки входа).
    """
    profiru_icon = "✅" if profiru_enabled else "⬜"
    kwork_icon = "✅" if kwork_enabled else "⬜"
    rows = [
        [
            InlineKeyboardButton(
                text=f"{profiru_icon} Профи.ру",
                callback_data=f"{_PREFIX}:toggle:profiru",
            ),
            InlineKeyboardButton(
                text="Настроить →",
                callback_data=f"{_PREFIX}:open:profiru",
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"{kwork_icon} Kwork",
                callback_data=f"{_PREFIX}:toggle:kwork",
            ),
            InlineKeyboardButton(
                text="Настроить →",
                callback_data=f"{_PREFIX}:open:kwork",
            ),
        ],
        [InlineKeyboardButton(text="← Назад", callback_data=back_cb)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Уровень 2: экран настроек конкретной платформы
# ---------------------------------------------------------------------------


def platform_detail_kb(
    platform: str,
    config: PlatformConfig,
) -> InlineKeyboardMarkup:
    """Экран настроек одной платформы — кнопка [изменить] на каждый фильтр."""
    rows: list[list[InlineKeyboardButton]] = []

    # Общие фильтры
    rows.append([
        InlineKeyboardButton(
            text=f"Бюджет: {_budget_label(config)}",
            callback_data=f"{_PREFIX}:edit:{platform}:budget",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text=f"Макс. возраст: {_age_label(config)}",
            callback_data=f"{_PREFIX}:edit:{platform}:age",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text=f"Мин. возраст: {_min_age_label(config)}",
            callback_data=f"{_PREFIX}:edit:{platform}:min_age",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text=f"Без бюджета: {'включать' if config.include_no_budget else 'пропускать'}",
            callback_data=f"{_PREFIX}:toggle_field:{platform}:include_no_budget",
        )
    ])

    # Platform-specific
    if platform == "profiru":
        rows.append([
            InlineKeyboardButton(
                text=f"Макс. цена отклика: {config.profiru.max_response_price or 'без ограничений'}",
                callback_data=f"{_PREFIX}:edit:{platform}:max_response_price",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=f"Только удалёнка: {'✅' if config.profiru.only_remote else '⬜'}",
                callback_data=f"{_PREFIX}:toggle_field:{platform}:only_remote",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=f"Исключать бесплатные: {'✅' if config.profiru.exclude_free else '⬜'}",
                callback_data=f"{_PREFIX}:toggle_field:{platform}:exclude_free",
            )
        ])
    elif platform == "kwork":
        rows.append([
            InlineKeyboardButton(
                text=f"Категории: {_kwork_cats_label(config)}",
                callback_data=f"{_PREFIX}:kwork_cats:open",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=f"Мин. % найма: {config.kwork.min_hired_percent if config.kwork.min_hired_percent is not None else 'не задан'}%",
                callback_data=f"{_PREFIX}:edit:{platform}:min_hired_percent",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=f"Диапазон откликов: {_offers_label(config.kwork)}",
                callback_data=f"{_PREFIX}:edit:{platform}:offers",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=f"Портфолио: {'исключать' if config.kwork.exclude_portfolio_required else 'любое'}",
                callback_data=f"{_PREFIX}:toggle_field:{platform}:exclude_portfolio_required",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=f"Мин. до дедлайна: {config.kwork.min_time_left_hours or 'не задан'} ч",
                callback_data=f"{_PREFIX}:edit:{platform}:min_time_left_hours",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="Сбросить фильтры",
            callback_data=f"{_PREFIX}:reset:{platform}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="← Назад к списку",
            callback_data=f"{_PREFIX}:back",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Kwork multi-select категорий
# ---------------------------------------------------------------------------


def kwork_categories_kb(
    tree: list[dict] | None,
    selected: list[int],
    *,
    parent_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Multi-select категорий Kwork через checkbox.

    tree: [{id, name, parent_id, children: [...]}]
    parent_id=None → top-level (parent_id is None в записях)
    parent_id=0    → sentinel "вернуться на top-level" (из drill callback)
    """
    rows: list[list[InlineKeyboardButton]] = []

    if tree is None:
        rows.append([
            InlineKeyboardButton(
                text="Загрузить дерево категорий",
                callback_data=f"{_PREFIX}:kwork_cats:refresh_tree",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="← Назад",
                callback_data=f"{_PREFIX}:open:kwork",
            )
        ])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    # Нормализуем parent_id: 0 → None (top-level)
    filter_pid: int | None = None if parent_id in (None, 0) else parent_id

    items = [n for n in tree if n.get("parent_id") == filter_pid]
    for item in items:
        icon = "✅" if item["id"] in selected else "⬜"
        label = f"{icon} {item['name']}"
        row = [
            InlineKeyboardButton(
                text=label,
                callback_data=f"{_PREFIX}:kwork_cats:toggle:{item['id']}",
            )
        ]
        if item.get("children"):
            row.append(
                InlineKeyboardButton(
                    text="▶",
                    callback_data=f"{_PREFIX}:kwork_cats:drill:{item['id']}",
                )
            )
        rows.append(row)

    # Навигационная строка
    nav_row: list[InlineKeyboardButton] = []
    if filter_pid is not None:
        nav_row.append(
            InlineKeyboardButton(
                text="← Наверх",
                callback_data=f"{_PREFIX}:kwork_cats:drill:0",
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text="Обновить",
            callback_data=f"{_PREFIX}:kwork_cats:refresh_tree",
        )
    )
    nav_row.append(
        InlineKeyboardButton(
            text="Сохранить",
            callback_data=f"{_PREFIX}:kwork_cats:save",
        )
    )
    rows.append(nav_row)
    rows.append([
        InlineKeyboardButton(
            text="← Назад к настройкам",
            callback_data=f"{_PREFIX}:open:kwork",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Helpers для текста кнопок
# ---------------------------------------------------------------------------


def _budget_label(c: PlatformConfig) -> str:
    if c.min_budget is None and c.max_budget is None:
        return "без ограничений"
    low = f"от {c.min_budget} руб." if c.min_budget is not None else ""
    high = f"до {c.max_budget} руб." if c.max_budget is not None else ""
    return f"{low} {high}".strip()


def _age_label(c: PlatformConfig) -> str:
    return f"до {c.max_age_hours} ч" if c.max_age_hours else "глобальный"


def _min_age_label(c: PlatformConfig) -> str:
    return f"от {c.min_age_seconds} сек" if c.min_age_seconds else "глобальный"


def _kwork_cats_label(c: PlatformConfig) -> str:
    ids = c.kwork.category_ids
    if not ids:
        return "пусто"
    if len(ids) <= 3:
        return ", ".join(str(i) for i in ids)
    return f"{', '.join(str(i) for i in ids[:3])}... ({len(ids)} шт.)"


def _offers_label(kw: KworkFilters) -> str:
    low = f">= {kw.min_offers}" if kw.min_offers is not None else ""
    high = f"<= {kw.max_offers}" if kw.max_offers is not None else ""
    if not low and not high:
        return "без ограничений"
    return f"{low} {high}".strip()
