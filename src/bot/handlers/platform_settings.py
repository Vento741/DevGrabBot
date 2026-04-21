"""Handlers платформенных настроек (Phase 4 v2.5).

Callback префиксы:
- mplat:* — глобальные фильтры из панели менеджера
- dplat:* — глобальные фильтры из панели разработчика (только admin)

Формат callback_data:
- {prefix}:toggle:{platform}            — включить/выключить платформу
- {prefix}:open:{platform}              — экран настроек уровня 2
- {prefix}:close                        — закрыть (удалить сообщение)
- {prefix}:back                         — назад к списку платформ
- {prefix}:edit:{platform}:{field}      — запрос ввода значения (FSM)
- {prefix}:toggle_field:{platform}:{f}  — переключить bool-поле
- {prefix}:reset:{platform}             — сбросить фильтры к дефолту
- {prefix}:kwork_cats:open              — multi-select категорий Kwork
- {prefix}:kwork_cats:toggle:{id}       — toggle категории в multi-select
- {prefix}:kwork_cats:drill:{id}        — провалиться в подкатегорию (0 = top)
- {prefix}:kwork_cats:save              — сохранить выбранные категории
- {prefix}:kwork_cats:refresh_tree      — обновить/загрузить дерево категорий
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from src.core.config import Settings
from src.core.models import TeamMember, TeamRole

from src.bot.keyboards.platforms import (
    kwork_categories_kb,
    platform_detail_kb,
    platforms_list_kb,
)
from src.bot.states import PlatformFilterStates
from src.core.platform_config import PlatformConfig
from src.core.settings_service import (
    get_kwork_categories_tree,
    get_platform_config,
    set_kwork_categories_tree,
    set_platform_config,
)

logger = logging.getLogger(__name__)
router = Router(name="platform_settings")

_ADMIN_TG_ID: int = Settings().admin_tg_id

# ---------------------------------------------------------------------------
# Access control helpers
# ---------------------------------------------------------------------------


def _is_privileged(member: TeamMember | None, tg_id: int | None) -> bool:
    """Права на управление глобальными фильтрами: manager или admin (Денис)."""
    if tg_id is not None and tg_id == _ADMIN_TG_ID:
        return True
    if member and member.role == TeamRole.manager:
        return True
    return False


async def _check_access(callback: CallbackQuery, session_factory: Any) -> bool:
    """Проверка: manager, либо admin по ADMIN_TG_ID. Иначе alert + False."""
    if not callback.from_user:
        await callback.answer("Доступ запрещён", show_alert=True)
        return False
    tg_id = callback.from_user.id
    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(TeamMember.tg_id == tg_id)
        )
        member = result.scalar_one_or_none()
    if not _is_privileged(member, tg_id):
        await callback.answer(
            "Только менеджер/админ может менять глобальные фильтры.",
            show_alert=True,
        )
        return False
    return True


async def _check_access_message(
    message: Message,
    session_factory: Any,
    state: FSMContext,
) -> bool:
    """Проверка роли для message-handlers FSM. При отказе — очищает state и отвечает."""
    if not message.from_user:
        await state.clear()
        await message.answer("Доступ запрещён.")
        return False
    tg_id = message.from_user.id
    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(TeamMember.tg_id == tg_id)
        )
        member = result.scalar_one_or_none()
    if not _is_privileged(member, tg_id):
        await state.clear()
        await message.answer("Доступ запрещён.")
        return False
    return True


# ---------------------------------------------------------------------------
# Fallback-дерево IT категорий Kwork (используется когда API недоступен)
# ---------------------------------------------------------------------------

_KWORK_IT_CATEGORIES_FALLBACK: list[dict] = [
    {"id": 11, "name": "Разработка и IT", "parent_id": None, "children": [41, 42, 43, 44, 45, 46, 47, 48]},
    {"id": 41, "name": "Сайты под ключ", "parent_id": 11, "children": []},
    {"id": 42, "name": "Доработка сайтов", "parent_id": 11, "children": []},
    {"id": 43, "name": "Интернет-магазины", "parent_id": 11, "children": []},
    {"id": 44, "name": "Мобильные приложения", "parent_id": 11, "children": []},
    {"id": 45, "name": "Боты и автоматизация", "parent_id": 11, "children": []},
    {"id": 46, "name": "Парсинг данных", "parent_id": 11, "children": []},
    {"id": 47, "name": "API и интеграции", "parent_id": 11, "children": []},
    {"id": 48, "name": "DevOps и администрирование", "parent_id": 11, "children": []},
]

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _owner_from_data(data: str) -> str:
    """mplat:... → 'mgr', dplat:... → 'dev'."""
    return "mgr" if data.startswith("mplat:") else "dev"


def _prefix_from_data(data: str) -> str:
    return "mplat" if data.startswith("mplat:") else "dplat"


def _platforms_text(profiru_cfg: PlatformConfig, kwork_cfg: PlatformConfig) -> str:
    return (
        "<b>Глобальные фильтры платформ</b>\n\n"
        f"Профи.ру: {'✅ Включена' if profiru_cfg.enabled else '⏸ Выключена'}\n"
        f"Kwork: {'✅ Включена' if kwork_cfg.enabled else '⏸ Выключена'}\n\n"
        "Нажмите «Настроить →» для управления фильтрами."
    )


def _platform_text(platform: str, config: PlatformConfig) -> str:
    """Краткий текст с текущими значениями фильтров платформы."""
    name_map = {"profiru": "Профи.ру", "kwork": "Kwork"}
    name = name_map.get(platform, platform)
    status = "Включена" if config.enabled else "Выключена"

    lines = [
        f"<b>Настройки: {name}</b>",
        f"Статус: {'✅ ' if config.enabled else '⏸ '}{status}",
        "",
        "<b>Общие фильтры:</b>",
        f"  Бюджет: {_budget_text(config)}",
        f"  Макс. возраст заявки: {f'{config.max_age_hours} ч' if config.max_age_hours else 'глобальный'}",
        f"  Мин. возраст заявки: {f'{config.min_age_seconds} сек' if config.min_age_seconds else 'глобальный'}",
        f"  Без бюджета: {'включать' if config.include_no_budget else 'пропускать'}",
    ]

    if platform == "profiru":
        lines += [
            "",
            "<b>Профи.ру:</b>",
            f"  Макс. цена отклика: {config.profiru.max_response_price or 'без ограничений'} руб.",
            f"  Только удалёнка: {'да' if config.profiru.only_remote else 'нет'}",
            f"  Исключать бесплатные: {'да' if config.profiru.exclude_free else 'нет'}",
        ]
    elif platform == "kwork":
        cats = config.kwork.category_ids
        cats_str = ", ".join(str(i) for i in cats) if cats else "не заданы"
        lines += [
            "",
            "<b>Kwork:</b>",
            f"  Категории: {cats_str}",
            f"  Мин. % найма: {config.kwork.min_hired_percent if config.kwork.min_hired_percent is not None else 'не задан'}%",
            f"  Диапазон откликов: {_offers_text(config)}",
            f"  Портфолио обязательно: {'исключать' if config.kwork.exclude_portfolio_required else 'любые'}",
            f"  Мин. до дедлайна: {f'{config.kwork.min_time_left_hours} ч' if config.kwork.min_time_left_hours else 'не задан'}",
        ]

    return "\n".join(lines)


def _budget_text(c: PlatformConfig) -> str:
    if c.min_budget is None and c.max_budget is None:
        return "без ограничений"
    parts = []
    if c.min_budget is not None:
        parts.append(f"от {c.min_budget} руб.")
    if c.max_budget is not None:
        parts.append(f"до {c.max_budget} руб.")
    return " ".join(parts)


def _offers_text(c: PlatformConfig) -> str:
    kw = c.kwork
    parts = []
    if kw.min_offers is not None:
        parts.append(f">= {kw.min_offers}")
    if kw.max_offers is not None:
        parts.append(f"<= {kw.max_offers}")
    return " ".join(parts) if parts else "без ограничений"


# ---------------------------------------------------------------------------
# Уровень 1: список платформ
# ---------------------------------------------------------------------------


async def _show_platforms_list(
    event: CallbackQuery | Message,
    session_factory: Any,
    owner: str,
    edit: bool = True,
) -> None:
    """Показать список платформ (уровень 1)."""
    async with session_factory() as session:
        profiru_cfg = await get_platform_config(session, "profiru")
        kwork_cfg = await get_platform_config(session, "kwork")

    text = _platforms_text(profiru_cfg, kwork_cfg)
    kb = platforms_list_kb(profiru_cfg.enabled, kwork_cfg.enabled, owner=owner)

    if isinstance(event, CallbackQuery):
        if edit and event.message:
            await event.message.edit_text(text, reply_markup=kb)
        elif event.message:
            await event.message.answer(text, reply_markup=kb)
    else:
        await event.answer(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Общий обработчик входа в раздел глобальных фильтров
# ---------------------------------------------------------------------------


@router.callback_query(F.data.in_({"mgr:global_filters", "dev:global_filters"}))
async def handle_open_global_filters(
    callback: CallbackQuery,
    session_factory: Any,
) -> None:
    """Открыть экран глобальных фильтров платформ из главного меню панели."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    owner = "mgr" if (callback.data or "").startswith("mgr:") else "dev"
    await _show_platforms_list(callback, session_factory, owner=owner)


# ---------------------------------------------------------------------------
# mplat:* и dplat:* handlers — toggle платформы
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.func(lambda d: d.startswith("mplat:toggle:") or d.startswith("dplat:toggle:"))
)
async def handle_toggle_platform(
    callback: CallbackQuery,
    session_factory: Any,
) -> None:
    """Включить/выключить платформу (глобально)."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        return
    platform = parts[2]
    owner = _owner_from_data(data)

    if platform not in {"profiru", "kwork"}:
        await callback.answer("Неизвестная платформа.", show_alert=True)
        return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        new_config = config.model_copy(update={"enabled": not config.enabled})
        await set_platform_config(session, platform, new_config)

    action = "включена" if new_config.enabled else "выключена"
    logger.info(
        "Платформа %s %s (owner=%s, tg_id=%s)",
        platform, action, owner,
        callback.from_user.id if callback.from_user else "?",
    )
    await callback.answer(f"Платформа {platform} {action}.", show_alert=False)
    await _show_platforms_list(callback, session_factory, owner=owner)


# ---------------------------------------------------------------------------
# open — уровень 2: экран настроек платформы
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.func(lambda d: d.startswith("mplat:open:") or d.startswith("dplat:open:"))
)
async def handle_open_platform(
    callback: CallbackQuery,
    session_factory: Any,
    state: FSMContext,
) -> None:
    """Открыть экран настроек конкретной платформы (уровень 2)."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    await state.clear()
    data = callback.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        return
    platform = parts[2]
    owner = _owner_from_data(data)

    if platform not in {"profiru", "kwork"}:
        return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)

    text = _platform_text(platform, config)
    kb = platform_detail_kb(platform, config, owner=owner)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# close — удалить сообщение
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.in_({"mplat:close", "dplat:close"})
)
async def handle_close_platforms(callback: CallbackQuery, session_factory: Any) -> None:
    """Удалить сообщение с настройками платформ."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# back — вернуться к списку платформ
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.in_({"mplat:back", "dplat:back"})
)
async def handle_back_to_list(
    callback: CallbackQuery,
    session_factory: Any,
    state: FSMContext,
) -> None:
    """Вернуться к списку платформ (уровень 1)."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    await state.clear()
    owner = _owner_from_data(callback.data or "")
    await _show_platforms_list(callback, session_factory, owner=owner)


# ---------------------------------------------------------------------------
# toggle_field — переключить bool-поле
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.func(
        lambda d: d.startswith("mplat:toggle_field:") or d.startswith("dplat:toggle_field:")
    )
)
async def handle_toggle_field(
    callback: CallbackQuery,
    session_factory: Any,
) -> None:
    """Инвертировать bool-поле в конфиге платформы."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    data = callback.data or ""
    # формат: {prefix}:toggle_field:{platform}:{field}
    parts = data.split(":")
    if len(parts) < 4:
        return
    owner = _owner_from_data(data)
    platform = parts[2]
    field = parts[3]

    if platform not in {"profiru", "kwork"}:
        return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        config_dict = config.model_dump()

        # Общие bool-поля
        if field == "include_no_budget":
            config_dict["include_no_budget"] = not config.include_no_budget
        # Profiru
        elif field == "only_remote" and platform == "profiru":
            config_dict["profiru"]["only_remote"] = not config.profiru.only_remote
        elif field == "exclude_free" and platform == "profiru":
            config_dict["profiru"]["exclude_free"] = not config.profiru.exclude_free
        # Kwork
        elif field == "exclude_portfolio_required" and platform == "kwork":
            config_dict["kwork"]["exclude_portfolio_required"] = not config.kwork.exclude_portfolio_required
        else:
            await callback.answer("Неизвестное поле.", show_alert=True)
            return

        new_config = PlatformConfig.model_validate(config_dict)
        await set_platform_config(session, platform, new_config)

    logger.info("Поле %s.%s переключено (owner=%s)", platform, field, owner)
    # Обновляем экран
    text = _platform_text(platform, new_config)
    kb = platform_detail_kb(platform, new_config, owner=owner)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# reset — сбросить фильтры платформы к дефолту
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.func(lambda d: d.startswith("mplat:reset:") or d.startswith("dplat:reset:"))
)
async def handle_reset_platform(
    callback: CallbackQuery,
    session_factory: Any,
) -> None:
    """Сбросить фильтры платформы к значениям по умолчанию."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        return
    platform = parts[2]
    owner = _owner_from_data(data)

    if platform not in {"profiru", "kwork"}:
        return

    default_config = PlatformConfig.default_for(platform)
    async with session_factory() as session:
        await set_platform_config(session, platform, default_config)

    logger.info("Фильтры платформы %s сброшены к дефолту (owner=%s)", platform, owner)
    await callback.answer("Фильтры сброшены.", show_alert=False)
    text = _platform_text(platform, default_config)
    kb = platform_detail_kb(platform, default_config, owner=owner)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# edit — запрос ввода значения (FSM)
# ---------------------------------------------------------------------------

_FIELD_PROMPTS: dict[str, str] = {
    "budget": (
        "Введите диапазон бюджета в формате <code>min max</code> (руб.).\n"
        "Примеры: <code>1000 50000</code>, <code>1000 -</code> (без макс.), <code>-</code> (снять ограничение)."
    ),
    "age": (
        "Введите максимальный возраст заявки в <b>часах</b> (целое число >= 1).\n"
        "Или <code>-</code> для использования глобального значения."
    ),
    "min_age": (
        "Введите минимальный возраст заявки в <b>секундах</b> (целое число >= 0).\n"
        "Или <code>-</code> для использования глобального значения."
    ),
    "max_response_price": (
        "Введите максимальную цену отклика в <b>рублях</b> (целое число).\n"
        "Или <code>-</code> для снятия ограничения."
    ),
    "min_hired_percent": (
        "Введите минимальный процент найма заказчика (0–100).\n"
        "Или <code>-</code> для снятия ограничения."
    ),
    "offers": (
        "Введите диапазон количества откликов в формате <code>min max</code>.\n"
        "Примеры: <code>0 10</code>, <code>- 20</code> (только макс.), <code>-</code> (снять ограничение)."
    ),
    "min_time_left_hours": (
        "Введите минимальное время до дедлайна в <b>часах</b>.\n"
        "Или <code>-</code> для снятия ограничения."
    ),
}

_FIELD_TO_STATE: dict[str, Any] = {
    "budget": PlatformFilterStates.waiting_budget,
    "age": PlatformFilterStates.waiting_age,
    "min_age": PlatformFilterStates.waiting_min_age,
    "max_response_price": PlatformFilterStates.waiting_max_response_price,
    "min_hired_percent": PlatformFilterStates.waiting_min_hired_percent,
    "offers": PlatformFilterStates.waiting_offers,
    "min_time_left_hours": PlatformFilterStates.waiting_min_time_left_hours,
}


@router.callback_query(
    F.data.func(lambda d: d.startswith("mplat:edit:") or d.startswith("dplat:edit:"))
)
async def handle_edit_field(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Перевести FSM в состояние ввода значения поля."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    data = callback.data or ""
    # формат: {prefix}:edit:{platform}:{field}
    parts = data.split(":")
    if len(parts) < 4:
        return
    owner = _owner_from_data(data)
    platform = parts[2]
    field = parts[3]

    fsm_state = _FIELD_TO_STATE.get(field)
    if not fsm_state:
        await callback.answer("Неизвестное поле для редактирования.", show_alert=True)
        return

    prompt = _FIELD_PROMPTS.get(field, "Введите новое значение:")
    await state.set_state(fsm_state)
    await state.update_data(platform=platform, field=field, owner=owner)

    from src.bot.keyboards.platforms import platform_detail_kb
    cancel_kb = _cancel_kb(owner, platform)
    if callback.message:
        await callback.message.answer(prompt, reply_markup=cancel_kb)


def _cancel_kb(owner: str, platform: str):
    """Клавиатура отмены ввода с возвратом на экран платформы."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    prefix = "mplat" if owner == "mgr" else "dplat"
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Отменить",
                callback_data=f"{prefix}:open:{platform}",
            )
        ]]
    )


# ---------------------------------------------------------------------------
# FSM: message handlers для ввода значений
# ---------------------------------------------------------------------------


@router.message(PlatformFilterStates.waiting_budget)
async def handle_input_budget(
    message: Message,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Обработать ввод диапазона бюджета."""
    if not await _check_access_message(message, session_factory, state):
        return
    data = await state.get_data()
    platform: str = data.get("platform", "profiru")
    owner: str = data.get("owner", "mgr")
    text = (message.text or "").strip()

    min_b: int | None = None
    max_b: int | None = None

    if text == "-":
        pass  # обе None
    else:
        parts = text.split()
        if len(parts) not in (1, 2):
            await message.answer(
                "Неверный формат. Ожидалось: <code>min max</code>, <code>min -</code> или <code>-</code>."
            )
            return
        try:
            min_b = None if parts[0] == "-" else int(parts[0])
            max_b = None if (len(parts) < 2 or parts[1] == "-") else int(parts[1])
            if min_b is not None and min_b < 0:
                raise ValueError
            if max_b is not None and max_b < 0:
                raise ValueError
            if min_b is not None and max_b is not None and max_b < min_b:
                await message.answer("Максимальный бюджет должен быть больше минимального.")
                return
        except ValueError:
            await message.answer(
                "Неверный формат. Ожидалось: <code>min max</code>, <code>min -</code> или <code>-</code>."
            )
            return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        new_config = config.model_copy(update={"min_budget": min_b, "max_budget": max_b})
        await set_platform_config(session, platform, new_config)

    await state.clear()
    logger.info("Бюджет платформы %s обновлён: %s–%s", platform, min_b, max_b)
    await message.answer("Бюджет обновлён.")
    await _send_platform_detail(message, session_factory, platform, owner)


@router.message(PlatformFilterStates.waiting_age)
async def handle_input_age(
    message: Message,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Обработать ввод максимального возраста заявки (часы)."""
    if not await _check_access_message(message, session_factory, state):
        return
    data = await state.get_data()
    platform: str = data.get("platform", "profiru")
    owner: str = data.get("owner", "mgr")
    text = (message.text or "").strip()

    value: int | None = None
    if text != "-":
        try:
            value = int(text)
            if value < 1:
                raise ValueError
        except ValueError:
            await message.answer("Неверный формат. Ожидалось: целое число >= 1 или <code>-</code>.")
            return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        new_config = config.model_copy(update={"max_age_hours": value})
        await set_platform_config(session, platform, new_config)

    await state.clear()
    logger.info("max_age_hours платформы %s = %s", platform, value)
    await message.answer("Макс. возраст обновлён.")
    await _send_platform_detail(message, session_factory, platform, owner)


@router.message(PlatformFilterStates.waiting_min_age)
async def handle_input_min_age(
    message: Message,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Обработать ввод минимального возраста заявки (секунды)."""
    if not await _check_access_message(message, session_factory, state):
        return
    data = await state.get_data()
    platform: str = data.get("platform", "profiru")
    owner: str = data.get("owner", "mgr")
    text = (message.text or "").strip()

    value: int | None = None
    if text != "-":
        try:
            value = int(text)
            if value < 0:
                raise ValueError
        except ValueError:
            await message.answer("Неверный формат. Ожидалось: целое число >= 0 или <code>-</code>.")
            return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        new_config = config.model_copy(update={"min_age_seconds": value})
        await set_platform_config(session, platform, new_config)

    await state.clear()
    logger.info("min_age_seconds платформы %s = %s", platform, value)
    await message.answer("Мин. возраст обновлён.")
    await _send_platform_detail(message, session_factory, platform, owner)


@router.message(PlatformFilterStates.waiting_max_response_price)
async def handle_input_max_response_price(
    message: Message,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Обработать ввод макс. цены отклика (Профи.ру)."""
    if not await _check_access_message(message, session_factory, state):
        return
    data = await state.get_data()
    platform: str = data.get("platform", "profiru")
    owner: str = data.get("owner", "mgr")
    text = (message.text or "").strip()

    value: int | None = None
    if text != "-":
        try:
            value = int(text)
            if value < 0:
                raise ValueError
        except ValueError:
            await message.answer("Неверный формат. Ожидалось: целое число >= 0 или <code>-</code>.")
            return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        config_dict = config.model_dump()
        config_dict["profiru"]["max_response_price"] = value
        new_config = PlatformConfig.model_validate(config_dict)
        await set_platform_config(session, platform, new_config)

    await state.clear()
    logger.info("max_response_price платформы %s = %s", platform, value)
    await message.answer("Макс. цена отклика обновлена.")
    await _send_platform_detail(message, session_factory, platform, owner)


@router.message(PlatformFilterStates.waiting_min_hired_percent)
async def handle_input_min_hired_percent(
    message: Message,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Обработать ввод мин. % найма (Kwork)."""
    if not await _check_access_message(message, session_factory, state):
        return
    data = await state.get_data()
    platform: str = data.get("platform", "kwork")
    owner: str = data.get("owner", "mgr")
    text = (message.text or "").strip()

    value: int | None = None
    if text != "-":
        try:
            value = int(text)
            if not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            await message.answer("Неверный формат. Ожидалось: число от 0 до 100 или <code>-</code>.")
            return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        config_dict = config.model_dump()
        config_dict["kwork"]["min_hired_percent"] = value
        new_config = PlatformConfig.model_validate(config_dict)
        await set_platform_config(session, platform, new_config)

    await state.clear()
    logger.info("min_hired_percent платформы %s = %s", platform, value)
    await message.answer("Мин. % найма обновлён.")
    await _send_platform_detail(message, session_factory, platform, owner)


@router.message(PlatformFilterStates.waiting_offers)
async def handle_input_offers(
    message: Message,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Обработать ввод диапазона количества откликов (Kwork)."""
    if not await _check_access_message(message, session_factory, state):
        return
    data = await state.get_data()
    platform: str = data.get("platform", "kwork")
    owner: str = data.get("owner", "mgr")
    text = (message.text or "").strip()

    min_o: int | None = None
    max_o: int | None = None

    if text == "-":
        pass
    else:
        parts = text.split()
        if len(parts) not in (1, 2):
            await message.answer(
                "Неверный формат. Ожидалось: <code>min max</code>, <code>- max</code> или <code>-</code>."
            )
            return
        try:
            min_o = None if parts[0] == "-" else int(parts[0])
            max_o = None if (len(parts) < 2 or parts[1] == "-") else int(parts[1])
            if min_o is not None and min_o < 0:
                raise ValueError
            if max_o is not None and max_o < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                "Неверный формат. Ожидалось: <code>min max</code>, <code>- max</code> или <code>-</code>."
            )
            return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        config_dict = config.model_dump()
        config_dict["kwork"]["min_offers"] = min_o
        config_dict["kwork"]["max_offers"] = max_o
        new_config = PlatformConfig.model_validate(config_dict)
        await set_platform_config(session, platform, new_config)

    await state.clear()
    logger.info("offers диапазон платформы %s: %s–%s", platform, min_o, max_o)
    await message.answer("Диапазон откликов обновлён.")
    await _send_platform_detail(message, session_factory, platform, owner)


@router.message(PlatformFilterStates.waiting_min_time_left_hours)
async def handle_input_min_time_left(
    message: Message,
    state: FSMContext,
    session_factory: Any,
) -> None:
    """Обработать ввод мин. времени до дедлайна (Kwork)."""
    if not await _check_access_message(message, session_factory, state):
        return
    data = await state.get_data()
    platform: str = data.get("platform", "kwork")
    owner: str = data.get("owner", "mgr")
    text = (message.text or "").strip()

    value: int | None = None
    if text != "-":
        try:
            value = int(text)
            if value < 0:
                raise ValueError
        except ValueError:
            await message.answer("Неверный формат. Ожидалось: целое число >= 0 или <code>-</code>.")
            return

    async with session_factory() as session:
        config = await get_platform_config(session, platform)
        config_dict = config.model_dump()
        config_dict["kwork"]["min_time_left_hours"] = value
        new_config = PlatformConfig.model_validate(config_dict)
        await set_platform_config(session, platform, new_config)

    await state.clear()
    logger.info("min_time_left_hours платформы %s = %s", platform, value)
    await message.answer("Мин. время до дедлайна обновлено.")
    await _send_platform_detail(message, session_factory, platform, owner)


async def _send_platform_detail(
    message: Message,
    session_factory: Any,
    platform: str,
    owner: str,
) -> None:
    """Отправить актуальный экран настроек платформы новым сообщением."""
    async with session_factory() as session:
        config = await get_platform_config(session, platform)
    text = _platform_text(platform, config)
    kb = platform_detail_kb(platform, config, owner=owner)
    await message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Kwork категории: open
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.in_({"mplat:kwork_cats:open", "dplat:kwork_cats:open"})
)
async def handle_kwork_cats_open(
    callback: CallbackQuery,
    session_factory: Any,
    state: FSMContext,
) -> None:
    """Открыть multi-select категорий Kwork."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    owner = _owner_from_data(callback.data or "")

    async with session_factory() as session:
        tree = await get_kwork_categories_tree(session)
        config = await get_platform_config(session, "kwork")

    selected = list(config.kwork.category_ids)
    # Сохраняем selected в FSM для накопления изменений
    await state.update_data(kwork_selected=selected, kwork_parent_id=None, owner=owner)

    text = "<b>Категории Kwork</b>\n\nВыберите нужные категории (чекбоксы). Нажмите «Сохранить»."
    kb = kwork_categories_kb(tree, selected, owner=owner, parent_id=None)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Kwork категории: toggle
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.func(
        lambda d: d.startswith("mplat:kwork_cats:toggle:") or d.startswith("dplat:kwork_cats:toggle:")
    )
)
async def handle_kwork_cats_toggle(
    callback: CallbackQuery,
    session_factory: Any,
    state: FSMContext,
) -> None:
    """Переключить категорию в multi-select."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    data = callback.data or ""
    # формат: {prefix}:kwork_cats:toggle:{id}
    parts = data.split(":")
    if len(parts) < 4:
        return
    owner = _owner_from_data(data)

    try:
        cat_id = int(parts[3])
    except ValueError:
        return

    fsm_data = await state.get_data()
    selected: list[int] = list(fsm_data.get("kwork_selected", []))
    parent_id: int | None = fsm_data.get("kwork_parent_id")

    if cat_id in selected:
        selected.remove(cat_id)
    else:
        selected.append(cat_id)

    await state.update_data(kwork_selected=selected)

    async with session_factory() as session:
        tree = await get_kwork_categories_tree(session)

    text = "<b>Категории Kwork</b>\n\nВыберите нужные категории (чекбоксы). Нажмите «Сохранить»."
    kb = kwork_categories_kb(tree, selected, owner=owner, parent_id=parent_id)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Kwork категории: drill (провалиться в подкатегорию)
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.func(
        lambda d: d.startswith("mplat:kwork_cats:drill:") or d.startswith("dplat:kwork_cats:drill:")
    )
)
async def handle_kwork_cats_drill(
    callback: CallbackQuery,
    session_factory: Any,
    state: FSMContext,
) -> None:
    """Переключить уровень дерева категорий."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 4:
        return
    owner = _owner_from_data(data)

    try:
        raw_pid = int(parts[3])
    except ValueError:
        return

    # 0 — sentinel для top-level
    parent_id: int | None = None if raw_pid == 0 else raw_pid
    await state.update_data(kwork_parent_id=parent_id)

    fsm_data = await state.get_data()
    selected: list[int] = list(fsm_data.get("kwork_selected", []))

    async with session_factory() as session:
        tree = await get_kwork_categories_tree(session)

    text = "<b>Категории Kwork</b>\n\nВыберите нужные категории (чекбоксы). Нажмите «Сохранить»."
    kb = kwork_categories_kb(tree, selected, owner=owner, parent_id=parent_id)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Kwork категории: save
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.in_({"mplat:kwork_cats:save", "dplat:kwork_cats:save"})
)
async def handle_kwork_cats_save(
    callback: CallbackQuery,
    session_factory: Any,
    state: FSMContext,
) -> None:
    """Сохранить выбранные категории в config.kwork.category_ids."""
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    fsm_data = await state.get_data()
    selected: list[int] = list(fsm_data.get("kwork_selected", []))
    owner = _owner_from_data(callback.data or "")
    await state.clear()

    async with session_factory() as session:
        config = await get_platform_config(session, "kwork")
        config_dict = config.model_dump()
        config_dict["kwork"]["category_ids"] = selected
        new_config = PlatformConfig.model_validate(config_dict)
        await set_platform_config(session, "kwork", new_config)

    logger.info("Категории Kwork обновлены: %s (owner=%s)", selected, owner)
    await callback.answer(f"Категории сохранены ({len(selected)} шт.).", show_alert=False)

    # Вернуться на экран настроек Kwork
    text = _platform_text("kwork", new_config)
    kb = platform_detail_kb("kwork", new_config, owner=owner)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)


# ---------------------------------------------------------------------------
# Kwork категории: refresh_tree (загрузить/обновить дерево)
# ---------------------------------------------------------------------------


@router.callback_query(
    F.data.in_({"mplat:kwork_cats:refresh_tree", "dplat:kwork_cats:refresh_tree"})
)
async def handle_kwork_cats_refresh_tree(
    callback: CallbackQuery,
    session_factory: Any,
    state: FSMContext,
) -> None:
    """Загрузить/обновить дерево категорий Kwork.

    TODO: Интегрировать реальный API pykwork когда появится метод get_categories().
    Сейчас использует hardcoded fallback IT-категорий.
    """
    if not await _check_access(callback, session_factory):
        return
    await callback.answer()
    owner = _owner_from_data(callback.data or "")

    # TODO: попытаться загрузить из pykwork API:
    # from kwork import Kwork
    # client = Kwork(login=settings.kwork_login, password=settings.kwork_password)
    # raw = await client.get_categories()  # метод может отсутствовать в текущей версии
    # tree = _normalize_kwork_tree(raw)

    # Fallback: hardcoded IT-категории
    tree = _KWORK_IT_CATEGORIES_FALLBACK
    async with session_factory() as session:
        await set_kwork_categories_tree(session, tree)
        config = await get_platform_config(session, "kwork")

    selected = list(config.kwork.category_ids)
    await state.update_data(kwork_selected=selected, kwork_parent_id=None, owner=owner)

    logger.info("Дерево категорий Kwork обновлено (fallback, %d категорий)", len(tree))
    await callback.answer("Дерево категорий обновлено (IT fallback).", show_alert=True)

    text = "<b>Категории Kwork</b>\n\nВыберите нужные категории (чекбоксы). Нажмите «Сохранить»."
    kb = kwork_categories_kb(tree, selected, owner=owner, parent_id=None)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=kb)
