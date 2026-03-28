"""Обработчики панели разработчика (CP-5.1, CP-5.4 — CP-5.11)."""
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.config import Settings
from src.core.models import (
    AiAnalysis,
    AssignmentStatus,
    Order,
    OrderAssignment,
    TeamMember,
    TeamRole,
)
from src.ai.context import OrderContext
from src.core.settings_service import (
    CONFIG_FALLBACK_KEYS,
    PROMPT_KEYS,
    add_stop_word,
    get_config_setting,
    get_prompt,
    get_stop_words,
    remove_stop_word,
    reset_prompt,
    set_prompt,
    set_setting,
)
from src.bot.states import DevPanelStates
from src.bot.keyboards.dev_panel import (
    back_to_dev_kb,
    cancel_dev_kb,
    dev_main_menu_kb,
    member_actions_kb,
    order_detail_kb,
    orders_filter_kb,
    orders_list_kb,
    prompt_actions_kb,
    prompts_list_kb,
    role_select_kb,
    settings_kb,
    stack_actions_kb,
    stop_words_kb,
    team_list_kb,
)
from src.bot.keyboards.review import approved_kb, review_actions_kb
from src.ai.prompts.analyze import SYSTEM_PROMPT as DEFAULT_ANALYZE
from src.ai.prompts.response import SYSTEM_PROMPT as DEFAULT_RESPONSE
from src.ai.prompts.roadmap import SYSTEM_PROMPT as DEFAULT_ROADMAP

logger = logging.getLogger(__name__)

router = Router(name="dev_panel")

_settings = Settings()
ADMIN_TG_ID: int = _settings.admin_tg_id


def _is_admin(tg_id: int | None) -> bool:
    """Проверить, является ли пользователь админом."""
    return tg_id == ADMIN_TG_ID

# Дефолтные промпты из файлов
DEFAULT_PROMPTS: dict[str, str] = {
    "analyze": DEFAULT_ANALYZE,
    "response": DEFAULT_RESPONSE,
    "roadmap": DEFAULT_ROADMAP,
}

# Человекочитаемые названия промптов
PROMPT_NAMES: dict[str, str] = {
    "analyze": "Анализ заявки",
    "response": "Генерация отклика",
    "roadmap": "Pre Roadmap",
}

# Человекочитаемые названия настроек
SETTING_NAMES: dict[str, str] = {
    "openrouter_model": "AI-модель",
    "parse_interval_sec": "Интервал парсинга (сек)",
    "time_threshold_hours": "Порог времени (ч)",
    "stats_broadcast_hour": "Час рассылки статистики (UTC, 0-23)",
}

_MAX_PROMPT_LEN = 3900


def _truncate(text: str, max_len: int = _MAX_PROMPT_LEN) -> str:
    """Обрезать текст до max_len символов с суффиксом '...'."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ---------------------------------------------------------------------------
# 1. Главное меню
# ---------------------------------------------------------------------------


@router.message(Command("dev"))
async def cmd_dev(message: Message) -> None:
    """Команда /dev — открыть панель разработчика."""
    tg_id = message.from_user.id if message.from_user else None
    await message.answer(
        "<b>Панель разработчика</b>\n\nВыберите раздел:",
        reply_markup=dev_main_menu_kb(is_admin=_is_admin(tg_id)),
    )
    logger.info(
        "Пользователь %s (tg_id=%s) открыл панель разработчика",
        message.from_user.full_name if message.from_user else "?",
        tg_id,
    )


@router.callback_query(F.data == "dev:back")
async def handle_dev_back(callback: CallbackQuery, state: FSMContext) -> None:
    """Вернуться в главное меню панели разработчика."""
    await state.clear()
    await callback.answer()
    tg_id = callback.from_user.id if callback.from_user else None
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Панель разработчика</b>\n\nВыберите раздел:",
        reply_markup=dev_main_menu_kb(is_admin=_is_admin(tg_id)),
    )


# ---------------------------------------------------------------------------
# 2. Мой стек
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "dev:stack")
async def handle_dev_stack(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    """Показать текущий стек разработчика."""
    await state.clear()
    await callback.answer()
    user_id = callback.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(TeamMember.tg_id == user_id)
        )
        member = result.scalar_one_or_none()

    if not member:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Вы не найдены в команде.",
            reply_markup=back_to_dev_kb(),
        )
        return

    primary = member.stack_priority.get("primary", []) if member.stack_priority else []
    secondary = member.stack_priority.get("secondary", []) if member.stack_priority else []

    if primary or secondary:
        primary_str = ", ".join(primary) if primary else "не указан"
        secondary_str = ", ".join(secondary) if secondary else "не указан"
        text = (
            "<b>Мой стек</b>\n\n"
            f"<b>Primary:</b> {primary_str}\n"
            f"<b>Secondary:</b> {secondary_str}"
        )
    else:
        text = "<b>Мой стек</b>\n\nСтек не указан"

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=stack_actions_kb(),
    )


@router.callback_query(F.data == "stack:edit_primary")
async def handle_stack_edit_primary(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать редактирование primary стека."""
    await callback.answer()
    await state.set_state(DevPanelStates.editing_primary_stack)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите <b>primary стек (основной)</b> через запятую\n"
        "(технологии, которые берёте уверенно — вес x2 при матчинге):\n\n"
        "<i>Например:</i> <code>Python, FastAPI, aiogram, Django</code>",
        reply_markup=cancel_dev_kb("dev:stack"),
    )


@router.callback_query(F.data == "stack:edit_secondary")
async def handle_stack_edit_secondary(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать редактирование secondary стека."""
    await callback.answer()
    await state.set_state(DevPanelStates.editing_secondary_stack)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите <b>secondary стек (дополнительный)</b> через запятую\n"
        "(умеете, но не основной фокус — вес x1 при матчинге):\n\n"
        "<i>Например:</i> <code>React, Next.js, MongoDB, LangChain</code>",
        reply_markup=cancel_dev_kb("dev:stack"),
    )


@router.message(DevPanelStates.editing_primary_stack)
async def process_primary_stack(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить primary стек.

    Если в state.data есть target_member_id — редактируем другого участника (через team:stack:).
    Иначе — редактируем себя по tg_id.
    """
    raw = message.text.strip() if message.text else ""
    if not raw:
        await message.answer("Пожалуйста, введите стек технологий:")
        return

    stack = [s.strip() for s in raw.split(",") if s.strip()]
    data = await state.get_data()
    target_member_id: int | None = data.get("target_member_id")
    user_id = message.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        if target_member_id is not None:
            # Редактируем другого участника
            result = await session.execute(
                select(TeamMember).where(TeamMember.id == target_member_id)
            )
        else:
            # Редактируем себя
            result = await session.execute(
                select(TeamMember).where(TeamMember.tg_id == user_id)
            )

        member = result.scalar_one_or_none()
        if not member:
            await message.answer("Участник не найден.")
            await state.clear()
            return

        current_priority = dict(member.stack_priority) if member.stack_priority else {}
        current_priority["primary"] = stack

        # Обновляем также tech_stack (объединение primary + secondary)
        secondary = current_priority.get("secondary", [])
        all_stack = list(dict.fromkeys(stack + secondary))  # уникальные, порядок сохранён

        member.stack_priority = current_priority
        member.tech_stack = all_stack
        member_name = member.name
        member_db_id = member.id
        await session.commit()

    await state.clear()

    if target_member_id is not None:
        await message.answer(
            f"Primary стек участника <b>{member_name}</b> обновлён: <b>{', '.join(stack)}</b>",
            reply_markup=member_actions_kb(member_db_id, True),
        )
        logger.info(
            "Primary стек участника %s (id=%s) обновлён: %s (tg_id=%s)",
            member_name, member_db_id, stack, user_id,
        )
    else:
        await message.answer(
            f"Primary стек обновлён: <b>{', '.join(stack)}</b>",
            reply_markup=stack_actions_kb(),
        )
        logger.info("tg_id=%s обновил primary стек: %s", user_id, stack)


@router.message(DevPanelStates.editing_secondary_stack)
async def process_secondary_stack(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить secondary стек.

    Если в state.data есть target_member_id — редактируем другого участника.
    Иначе — редактируем себя по tg_id.
    """
    raw = message.text.strip() if message.text else ""
    if not raw:
        await message.answer("Пожалуйста, введите стек технологий:")
        return

    stack = [s.strip() for s in raw.split(",") if s.strip()]
    data = await state.get_data()
    target_member_id: int | None = data.get("target_member_id")
    user_id = message.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        if target_member_id is not None:
            result = await session.execute(
                select(TeamMember).where(TeamMember.id == target_member_id)
            )
        else:
            result = await session.execute(
                select(TeamMember).where(TeamMember.tg_id == user_id)
            )

        member = result.scalar_one_or_none()
        if not member:
            await message.answer("Участник не найден.")
            await state.clear()
            return

        current_priority = dict(member.stack_priority) if member.stack_priority else {}
        current_priority["secondary"] = stack

        primary = current_priority.get("primary", [])
        all_stack = list(dict.fromkeys(primary + stack))

        member.stack_priority = current_priority
        member.tech_stack = all_stack
        member_name = member.name
        member_db_id = member.id
        await session.commit()

    await state.clear()

    if target_member_id is not None:
        await message.answer(
            f"Secondary стек участника <b>{member_name}</b> обновлён: <b>{', '.join(stack)}</b>",
            reply_markup=member_actions_kb(member_db_id, True),
        )
        logger.info(
            "Secondary стек участника %s (id=%s) обновлён: %s (tg_id=%s)",
            member_name, member_db_id, stack, user_id,
        )
    else:
        await message.answer(
            f"Secondary стек обновлён: <b>{', '.join(stack)}</b>",
            reply_markup=stack_actions_kb(),
        )
        logger.info("tg_id=%s обновил secondary стек: %s", user_id, stack)


@router.callback_query(F.data == "stack:clear")
async def handle_stack_clear(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Очистить весь стек разработчика."""
    await callback.answer()
    user_id = callback.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(TeamMember.tg_id == user_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            await callback.message.edit_text(  # type: ignore[union-attr]
                "Вы не найдены в команде.",
                reply_markup=back_to_dev_kb(),
            )
            return

        member.stack_priority = {}
        member.tech_stack = []
        await session.commit()

    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Мой стек</b>\n\nСтек очищен.",
        reply_markup=stack_actions_kb(),
    )
    logger.info("tg_id=%s очистил стек", user_id)


# ---------------------------------------------------------------------------
# 3. Стоп-слова
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "dev:stopwords")
async def handle_dev_stopwords(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    state: FSMContext,
) -> None:
    """Показать список стоп-слов (только админ)."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступно только админу", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    async with session_factory() as session:
        words = await get_stop_words(session, settings)

    count = len(words)
    header = f"<b>Стоп-слова</b> ({count}):\n\n" if count else "<b>Стоп-слова</b>\n\nСписок пуст.\n\n"
    if words:
        header += "\n".join(f"  • {w}" for w in words) + "\n\n"
    header += "Нажмите на слово для удаления или добавьте новое:"

    await callback.message.edit_text(  # type: ignore[union-attr]
        header,
        reply_markup=stop_words_kb(words),
    )


@router.callback_query(F.data.startswith("sw:del:"))
async def handle_sw_delete(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Удалить стоп-слово."""
    await callback.answer()
    word = callback.data.split("sw:del:", 1)[1]  # type: ignore[union-attr]

    async with session_factory() as session:
        words = await remove_stop_word(session, word)

    count = len(words)
    header = f"<b>Стоп-слова</b> ({count}):\n\n" if count else "<b>Стоп-слова</b>\n\nСписок пуст.\n\n"
    if words:
        header += "\n".join(f"  • {w}" for w in words) + "\n\n"
    header += "Нажмите на слово для удаления или добавьте новое:"

    await callback.message.edit_text(  # type: ignore[union-attr]
        header,
        reply_markup=stop_words_kb(words),
    )
    logger.info("Стоп-слово удалено: %s", word)


@router.callback_query(F.data == "sw:add")
async def handle_sw_add_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать добавление стоп-слова."""
    await callback.answer()
    await state.set_state(DevPanelStates.adding_stop_word)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите стоп-слово для добавления:",
        reply_markup=cancel_dev_kb("dev:stopwords"),
    )


@router.message(DevPanelStates.adding_stop_word)
async def process_add_stop_word(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Сохранить новое стоп-слово."""
    word = message.text.strip() if message.text else ""
    if not word:
        await message.answer("Пожалуйста, введите слово:")
        return

    async with session_factory() as session:
        words = await add_stop_word(session, word)

    await state.clear()

    count = len(words)
    header = f"<b>Стоп-слова</b> ({count}):\n\n"
    if words:
        header += "\n".join(f"  • {w}" for w in words) + "\n\n"
    header += "Нажмите на слово для удаления или добавьте новое:"

    await message.answer(
        header,
        reply_markup=stop_words_kb(words),
    )
    logger.info("Добавлено стоп-слово: %s", word)


# ---------------------------------------------------------------------------
# 4. Промпты
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "dev:prompts")
async def handle_dev_prompts(callback: CallbackQuery, state: FSMContext) -> None:
    """Показать список промптов (только админ)."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступно только админу", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Управление промптами</b>\n\nВыберите промпт для просмотра или редактирования:",
        reply_markup=prompts_list_kb(),
    )


@router.callback_query(F.data.regexp(r"^prompt:(analyze|response|roadmap)$"))
async def handle_prompt_view(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать текущий текст выбранного промпта."""
    await callback.answer()
    prompt_key = callback.data.split(":")[1]  # type: ignore[union-attr]

    async with session_factory() as session:
        custom = await get_prompt(session, prompt_key)

    if custom is not None:
        source_label = "(кастомный)"
        text = custom
    else:
        source_label = "(по умолчанию)"
        text = DEFAULT_PROMPTS.get(prompt_key, "")

    prompt_name = PROMPT_NAMES.get(prompt_key, prompt_key)
    truncated = _truncate(text)
    display = (
        f"<b>Промпт: {prompt_name}</b> <i>{source_label}</i>\n\n"
        f"<code>{truncated}</code>"
    )

    await callback.message.edit_text(  # type: ignore[union-attr]
        display,
        reply_markup=prompt_actions_kb(prompt_key),
    )


@router.callback_query(F.data.startswith("prompt:edit:"))
async def handle_prompt_edit_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать редактирование промпта."""
    await callback.answer()
    prompt_key = callback.data.split("prompt:edit:", 1)[1]  # type: ignore[union-attr]
    await state.update_data(prompt_key=prompt_key)
    await state.set_state(DevPanelStates.editing_prompt)

    prompt_name = PROMPT_NAMES.get(prompt_key, prompt_key)
    await callback.message.answer(  # type: ignore[union-attr]
        f"Введите новый текст для промпта <b>{prompt_name}</b>:\n\n"
        "<i>Максимальная длина — 3900 символов. "
        "Отправьте текст одним сообщением.</i>",
        reply_markup=cancel_dev_kb("dev:prompts"),
    )


@router.message(DevPanelStates.editing_prompt)
async def process_edit_prompt(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить новый текст промпта."""
    text = message.text.strip() if message.text else ""
    if not text:
        await message.answer("Пожалуйста, введите текст промпта:")
        return

    data = await state.get_data()
    prompt_key = data.get("prompt_key", "")

    async with session_factory() as session:
        await set_prompt(session, prompt_key, text)

    await state.clear()

    prompt_name = PROMPT_NAMES.get(prompt_key, prompt_key)
    await message.answer(
        f"Промпт <b>{prompt_name}</b> обновлён.\n\n"
        f"Длина: {len(text)} символов.",
        reply_markup=prompt_actions_kb(prompt_key),
    )
    logger.info("Промпт '%s' обновлён, длина=%d", prompt_key, len(text))


@router.callback_query(F.data.startswith("prompt:reset:"))
async def handle_prompt_reset(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сбросить промпт к файловому дефолту."""
    await callback.answer()
    prompt_key = callback.data.split("prompt:reset:", 1)[1]  # type: ignore[union-attr]

    async with session_factory() as session:
        await reset_prompt(session, prompt_key)

    prompt_name = PROMPT_NAMES.get(prompt_key, prompt_key)
    default_text = DEFAULT_PROMPTS.get(prompt_key, "")
    truncated = _truncate(default_text)

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"<b>Промпт: {prompt_name}</b> <i>(по умолчанию)</i>\n\n"
        f"Промпт сброшен к значению по умолчанию.\n\n"
        f"<code>{truncated}</code>",
        reply_markup=prompt_actions_kb(prompt_key),
    )
    logger.info("Промпт '%s' сброшен к дефолту", prompt_key)


# ---------------------------------------------------------------------------
# 5. Команда
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "dev:team")
async def handle_dev_team(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    """Показать список участников команды."""
    await state.clear()
    await callback.answer()
    is_admin = _is_admin(callback.from_user.id)

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).order_by(TeamMember.role, TeamMember.name)
        )
        members = list(result.scalars().all())

    count = len(members)
    active = sum(1 for m in members if m.is_active)
    subtitle = "Выберите участника для управления:" if is_admin else "Обзор команды:"
    text = f"<b>Команда</b> ({count} участников, {active} активных)\n\n{subtitle}"
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=team_list_kb(members, show_add=is_admin),
    )


@router.callback_query(F.data.startswith("team:member:"))
async def handle_team_member(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать детали участника команды."""
    await callback.answer()
    member_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(TeamMember.id == member_id)
        )
        member = result.scalar_one_or_none()

    if not member:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Участник не найден.",
            reply_markup=back_to_dev_kb(),
        )
        logger.warning("Участник team_member.id=%s не найден", member_id)
        return

    primary = member.stack_priority.get("primary", []) if member.stack_priority else []
    secondary = member.stack_priority.get("secondary", []) if member.stack_priority else []
    primary_str = ", ".join(primary) if primary else "—"
    secondary_str = ", ".join(secondary) if secondary else "—"
    status_str = "активен" if member.is_active else "деактивирован"
    username_str = f"@{member.tg_username}" if member.tg_username else "—"

    is_dev = member.role == TeamRole.developer

    text = (
        f"<b>Участник: {member.name}</b>\n\n"
        f"<b>Роль:</b> {member.role.value}\n"
        f"<b>Telegram:</b> {username_str} (ID: {member.tg_id})\n"
        f"<b>Статус:</b> {status_str}"
    )
    if is_dev:
        text += (
            f"\n\n<b>Primary стек:</b> {primary_str}\n"
            f"<b>Secondary стек:</b> {secondary_str}"
        )

    if _is_admin(callback.from_user.id):
        kb = member_actions_kb(member_id, member.is_active, show_stack=is_dev)
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="<- Назад", callback_data="dev:team")],
        ])

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("team:toggle:"))
async def handle_team_toggle(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Переключить активность участника команды."""
    await callback.answer()
    member_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(TeamMember.id == member_id)
        )
        member = result.scalar_one_or_none()

        if not member:
            await callback.message.edit_text(  # type: ignore[union-attr]
                "Участник не найден.",
                reply_markup=back_to_dev_kb(),
            )
            logger.warning("Попытка toggle несуществующего участника id=%s", member_id)
            return

        member.is_active = not member.is_active
        new_status = member.is_active
        member_name = member.name
        await session.commit()

    status_str = "активирован" if new_status else "деактивирован"
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"Участник <b>{member_name}</b> {status_str}.",
        reply_markup=member_actions_kb(member_id, new_status),
    )
    logger.info(
        "Участник %s (id=%s) %s",
        member_name, member_id, status_str,
    )


@router.callback_query(F.data.startswith("team:stack_primary:"))
async def handle_team_stack_primary_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать редактирование primary стека участника."""
    await callback.answer()
    member_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]
    await state.update_data(target_member_id=member_id, stack_type="primary")
    await state.set_state(DevPanelStates.editing_primary_stack)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите <b>primary стек (основной)</b> участника через запятую\n"
        "(технологии, которые берёт уверенно — вес x2 при матчинге):\n\n"
        "<i>Например:</i> <code>Python, FastAPI, aiogram, Django</code>",
        reply_markup=cancel_dev_kb("dev:team"),
    )


@router.callback_query(F.data.startswith("team:stack_secondary:"))
async def handle_team_stack_secondary_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать редактирование secondary стека участника."""
    await callback.answer()
    member_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]
    await state.update_data(target_member_id=member_id, stack_type="secondary")
    await state.set_state(DevPanelStates.editing_secondary_stack)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите <b>secondary стек (дополнительный)</b> участника через запятую\n"
        "(умеет, но не основной фокус — вес x1 при матчинге):\n\n"
        "<i>Например:</i> <code>React, Next.js, MongoDB, LangChain</code>",
        reply_markup=cancel_dev_kb("dev:team"),
    )


@router.callback_query(F.data == "team:add")
async def handle_team_add_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать процесс добавления нового участника команды."""
    await callback.answer()
    await state.set_state(DevPanelStates.adding_member_tg_id)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите <b>Telegram ID</b> нового участника\n"
        "(числовой ID, например: <code>123456789</code>):",
        reply_markup=cancel_dev_kb("dev:team"),
    )


@router.message(DevPanelStates.adding_member_tg_id)
async def process_add_member_tg_id(
    message: Message,
    state: FSMContext,
) -> None:
    """Получить Telegram ID нового участника."""
    raw = message.text.strip() if message.text else ""
    if not raw.lstrip("-").isdigit():
        await message.answer("Пожалуйста, введите числовой Telegram ID:")
        return

    tg_id = int(raw)
    await state.update_data(new_member_tg_id=tg_id)
    await state.set_state(DevPanelStates.adding_member_role)
    await message.answer(
        f"Telegram ID: <code>{tg_id}</code>\n\nВыберите роль нового участника:",
        reply_markup=role_select_kb(),
    )


@router.callback_query(F.data.startswith("role:"))
async def handle_role_select(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Получить роль нового участника."""
    await callback.answer()
    role_str = callback.data.split(":")[1]  # type: ignore[union-attr]

    if role_str not in ("developer", "manager"):
        await callback.answer("Неверная роль.", show_alert=True)
        return

    await state.update_data(new_member_role=role_str)
    await state.set_state(DevPanelStates.adding_member_name)
    await callback.message.answer(  # type: ignore[union-attr]
        f"Роль: <b>{role_str}</b>\n\nВведите имя участника:",
        reply_markup=cancel_dev_kb("dev:team"),
    )


@router.message(DevPanelStates.adding_member_name)
async def process_add_member_name(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить нового участника команды."""
    name = message.text.strip() if message.text else ""
    if not name:
        await message.answer("Пожалуйста, введите имя:")
        return

    data = await state.get_data()
    tg_id: int = data["new_member_tg_id"]
    role_str: str = data["new_member_role"]
    role = TeamRole.developer if role_str == "developer" else TeamRole.manager

    async with session_factory() as session:
        # Проверить, не существует ли уже такой tg_id
        existing = await session.execute(
            select(TeamMember).where(TeamMember.tg_id == tg_id)
        )
        if existing.scalar_one_or_none():
            await message.answer(
                f"Участник с Telegram ID <code>{tg_id}</code> уже существует в команде.",
                reply_markup=back_to_dev_kb(),
            )
            await state.clear()
            return

        new_member = TeamMember(
            tg_id=tg_id,
            name=name,
            role=role,
            is_active=True,
            tech_stack=[],
            stack_priority={},
        )
        session.add(new_member)
        await session.commit()
        await session.refresh(new_member)
        new_id = new_member.id

    await state.clear()
    await message.answer(
        f"Участник <b>{name}</b> добавлен в команду.\n"
        f"Роль: {role_str} | ID: {new_id}",
        reply_markup=member_actions_kb(new_id, True),
    )
    logger.info(
        "Добавлен новый участник команды: %s, tg_id=%s, role=%s",
        name, tg_id, role_str,
    )


# ---------------------------------------------------------------------------
# 6. Настройки
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "dev:settings")
async def handle_dev_settings(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    state: FSMContext,
) -> None:
    """Показать текущие настройки бота (только админ)."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("Доступно только админу", show_alert=True)
        return
    await state.clear()
    await callback.answer()

    async with session_factory() as session:
        current: dict[str, str] = {}
        for key in CONFIG_FALLBACK_KEYS:
            current[key] = await get_config_setting(session, key, settings)

    # Загружаем статус уведомлений для текущего разработчика
    user_id = callback.from_user.id
    async with session_factory() as sess2:
        mem_result = await sess2.execute(
            select(TeamMember).where(TeamMember.tg_id == user_id)
        )
        mem = mem_result.scalar_one_or_none()
    notify_status = "ВКЛ" if (mem and mem.notify_assignments) else "ВЫКЛ"

    text = (
        "<b>Настройки бота</b>\n\n"
        f"<b>AI-модель:</b> {current.get('openrouter_model', '—')}\n"
        f"<b>Интервал парсинга:</b> {current.get('parse_interval_sec', '—')} сек\n"
        f"<b>Порог времени:</b> {current.get('time_threshold_hours', '—')} ч\n"
        f"<b>Час рассылки статистики:</b> {current.get('stats_broadcast_hour', '9')} UTC\n"
        f"\n<b>Уведомления о взятых заявках:</b> {notify_status}\n\n"
        "Нажмите на параметр для изменения:"
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=settings_kb({**current, "notify_label": notify_status}),
    )


@router.callback_query(F.data == "dev:toggle_notify")
async def handle_toggle_notify(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    state: FSMContext,
) -> None:
    """Переключить уведомления о взятых заявках."""
    await callback.answer()
    user_id = callback.from_user.id

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember).where(TeamMember.tg_id == user_id)
        )
        member = result.scalar_one_or_none()
        if not member:
            await callback.answer("Вы не в команде.", show_alert=True)
            return

        member.notify_assignments = not member.notify_assignments
        new_status = member.notify_assignments
        await session.commit()

    notify_status = "ВКЛ" if new_status else "ВЫКЛ"
    await callback.answer(f"Уведомления: {notify_status}", show_alert=True)

    # Обновляем экран настроек
    async with session_factory() as session:
        current: dict[str, str] = {}
        for key in CONFIG_FALLBACK_KEYS:
            current[key] = await get_config_setting(session, key, settings)

    text = (
        "<b>Настройки бота</b>\n\n"
        f"<b>AI-модель:</b> {current.get('openrouter_model', '—')}\n"
        f"<b>Интервал парсинга:</b> {current.get('parse_interval_sec', '—')} сек\n"
        f"<b>Порог времени:</b> {current.get('time_threshold_hours', '—')} ч\n"
        f"<b>Час рассылки статистики:</b> {current.get('stats_broadcast_hour', '9')} UTC\n"
        f"\n<b>Уведомления о взятых заявках:</b> {notify_status}\n\n"
        "Нажмите на параметр для изменения:"
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=settings_kb({**current, "notify_label": notify_status}),
    )


@router.callback_query(F.data.startswith("set:"))
async def handle_setting_edit_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начать редактирование настройки."""
    await callback.answer()
    key = callback.data.split("set:", 1)[1]  # type: ignore[union-attr]

    if key not in CONFIG_FALLBACK_KEYS:
        await callback.answer("Неизвестный параметр.", show_alert=True)
        return

    await state.update_data(setting_key=key)
    await state.set_state(DevPanelStates.editing_setting_value)

    param_name = SETTING_NAMES.get(key, key)
    await callback.message.answer(  # type: ignore[union-attr]
        f"Введите новое значение для <b>{param_name}</b>:",
        reply_markup=cancel_dev_kb("dev:settings"),
    )


@router.message(DevPanelStates.editing_setting_value)
async def process_setting_value(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Сохранить новое значение настройки."""
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Пожалуйста, введите значение:")
        return

    data = await state.get_data()
    key: str = data["setting_key"]

    async with session_factory() as session:
        await set_setting(session, key, value)
        current: dict[str, str] = {}
        for k in CONFIG_FALLBACK_KEYS:
            current[k] = await get_config_setting(session, k, settings)

    await state.clear()

    param_name = SETTING_NAMES.get(key, key)
    text = (
        f"Параметр <b>{param_name}</b> обновлён: <code>{value}</code>\n\n"
        "<b>Текущие настройки:</b>\n"
        f"<b>AI-модель:</b> {current.get('openrouter_model', '—')}\n"
        f"<b>Интервал парсинга:</b> {current.get('parse_interval_sec', '—')} сек\n"
        f"<b>Порог времени:</b> {current.get('time_threshold_hours', '—')} ч"
    )
    await message.answer(
        text,
        reply_markup=settings_kb(current),
    )
    logger.info("Настройка '%s' обновлена: %s", key, value)


# ---------------------------------------------------------------------------
# 7. Мои заявки
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "dev:orders")
async def handle_dev_orders(callback: CallbackQuery) -> None:
    """Показать фильтр заявок."""
    await callback.answer()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Мои заявки</b>\n\nВыберите фильтр:",
        reply_markup=orders_filter_kb(),
    )


async def _fetch_member_assignments(
    session: AsyncSession,
    tg_id: int,
    statuses: list[AssignmentStatus] | None = None,
) -> tuple[TeamMember | None, list[OrderAssignment]]:
    """Загрузить участника и его назначения с заявками."""
    member_result = await session.execute(
        select(TeamMember).where(TeamMember.tg_id == tg_id)
    )
    member = member_result.scalar_one_or_none()
    if not member:
        return None, []

    query = (
        select(OrderAssignment)
        .options(selectinload(OrderAssignment.order))
        .where(OrderAssignment.developer_id == member.id)
    )
    if statuses:
        query = query.where(OrderAssignment.status.in_(statuses))

    result = await session.execute(query)
    assignments = list(result.scalars().all())
    return member, assignments


def _format_assignments_list(assignments: list[OrderAssignment], title: str) -> str:
    """Сформировать HTML-список назначений."""
    count = len(assignments)
    if not count:
        return f"<b>{title}:</b> 0\n\nЗаявок нет."

    lines = [f"<b>{title}:</b> {count}\n"]
    for i, a in enumerate(assignments, start=1):
        order = a.order
        price_str = f"{a.price_final:,} руб.".replace(",", " ") if a.price_final else "—"
        timeline_str = a.timeline_final or "—"
        lines.append(
            f"{i}. {order.external_id} — {order.title}\n"
            f"   Цена: {price_str} | Сроки: {timeline_str}\n"
            f"   Статус: {a.status.value}"
        )

    return "\n\n".join(lines)


async def _show_orders_tab(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    statuses: list[AssignmentStatus] | None,
    title: str,
    active_filter: str,
) -> None:
    """Общий хелпер для показа вкладки заявок."""
    await callback.answer()
    user_id = callback.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        member, assignments = await _fetch_member_assignments(session, user_id, statuses)

    if not member:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Вы не найдены в команде.",
            reply_markup=back_to_dev_kb(),
        )
        return

    count = len(assignments)
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"<b>Мои заявки ({title}):</b> {count}",
        reply_markup=orders_list_kb(assignments, active_filter=active_filter),
    )


@router.callback_query(F.data == "orders:in_progress")
async def handle_orders_in_progress(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать заявки 'в работе'."""
    await _show_orders_tab(
        callback, session_factory,
        statuses=[AssignmentStatus.in_progress],
        title="в работе",
        active_filter="in_progress",
    )


@router.callback_query(F.data == "orders:sent")
async def handle_orders_sent(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать отправленные заявки (ожидают PM / отправлены клиенту)."""
    await _show_orders_tab(
        callback, session_factory,
        statuses=[AssignmentStatus.approved, AssignmentStatus.sent],
        title="отправленные",
        active_filter="sent",
    )


@router.callback_query(F.data == "orders:cancelled")
async def handle_orders_cancelled(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать отменённые/архивные заявки."""
    await _show_orders_tab(
        callback, session_factory,
        statuses=[AssignmentStatus.cancelled],
        title="архив",
        active_filter="cancelled",
    )


@router.callback_query(F.data == "orders:all")
async def handle_orders_all(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать все заявки."""
    await _show_orders_tab(
        callback, session_factory,
        statuses=None,
        title="все",
        active_filter="all",
    )


_STATUS_LABELS: dict[str, str] = {
    "sent": "\u2705 Отправлен клиенту",
    "in_progress": "\U0001f528 В работе",
    "cancelled": "\u274c Отменена",
    "approved": "\U0001f4cb Утверждён (ожидает PM)",
    "pending": "\u23f3 Ожидает",
    "editing": "\u270f\ufe0f Редактируется",
    "rejected": "\U0001f6ab Отклонена",
    "reassigned": "\U0001f504 Переназначена",
}


def _format_price_range(
    price_min: int | None, price_max: int | None, fallback: str = "—",
) -> str:
    """Форматирование диапазона цен."""
    parts: list[str] = []
    if price_min:
        parts.append(f"от {price_min:,}".replace(",", " "))
    if price_max:
        parts.append(f"до {price_max:,}".replace(",", " "))
    return " ".join(parts) + " руб." if parts else fallback


@router.callback_query(F.data.startswith("dev_order:"))
async def handle_dev_order_detail(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать полную детальную карточку заявки (как при первом взятии)."""
    await callback.answer()
    assignment_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(OrderAssignment)
            .options(
                selectinload(OrderAssignment.order).selectinload(Order.analyses),
                selectinload(OrderAssignment.manager_response),
            )
            .where(OrderAssignment.id == assignment_id)
        )
        assignment = result.scalar_one_or_none()

    if not assignment:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Назначение не найдено.",
            reply_markup=back_to_dev_kb(),
        )
        return

    order = assignment.order
    analysis: AiAnalysis | None = order.analyses[0] if order.analyses else None
    status_badge = _STATUS_LABELS.get(assignment.status.value, assignment.status.value)

    # Строим полную карточку через OrderContext (как в review)
    ctx = OrderContext.from_order_data(order, analysis, assignment)

    stack_str = ", ".join(ctx.effective_stack) if ctx.effective_stack else "—"
    price_display = _format_price_range(ctx.price_min, ctx.price_max, fallback="—")
    response_price_str = f"{order.response_price} руб." if order.response_price else "—"

    text = (
        f"<b>Заявка:</b> {order.external_id} — {order.title}\n"
        f"<b>Статус:</b> {status_badge}\n\n"
    )

    # AI-выжимка
    if ctx.summary:
        text += f"<b>AI-выжимка:</b>\n{ctx.summary}\n\n"

    # Пожелания заказчика
    if ctx.has_client_requirements:
        text += f"<b>Пожелания заказчика:</b>\n{ctx.client_requirements}\n\n"

    # Основные параметры
    text += (
        f"<b>Стек:</b> {stack_str}\n"
        f"<b>AI-оценка цены:</b> {price_display}\n"
        f"<b>Цена:</b> {ctx.effective_price or '—'} руб.\n"
        f"<b>Сроки:</b> {ctx.effective_timeline} дн.\n"
        f"<b>Сложность:</b> {ctx.complexity}\n"
        f"<b>Цена отклика:</b> {response_price_str}\n"
    )

    # Материалы (вложения)
    if order.materials:
        text += f"\n\U0001f4ce <b>Материалы:</b> {len(order.materials)} шт.\n"

    # Вопросы клиенту
    if ctx.questions:
        q_list = "\n".join(f"  - {q}" for q in ctx.questions[:5])
        text += f"\n<b>Вопросы клиенту:</b>\n{q_list}\n"

    # Риски
    if ctx.risks and ctx.risks != "Нет явных рисков":
        text += f"\n<b>Риски:</b> {ctx.risks}\n"

    # Заметки разработчика
    if assignment.custom_notes:
        text += f"\n<b>Заметка:</b> {assignment.custom_notes}\n"

    # Черновик отклика / финальный текст
    response_text = ""
    if assignment.manager_response:
        response_text = (
            assignment.manager_response.edited_text
            or assignment.manager_response.response_text
            or ""
        )
    if not response_text and ctx.response_draft:
        response_text = ctx.response_draft

    if response_text:
        if len(response_text) > 2000:
            response_text = response_text[:2000] + "..."
        text += f"\n<b>Черновик отклика:</b>\n<i>{response_text}</i>"

    # Выбираем клавиатуру в зависимости от статуса
    if assignment.status in (AssignmentStatus.pending, AssignmentStatus.editing):
        # Заявка ещё редактируется — полная review-клавиатура
        text += "\n\nОтредактируйте параметры или утвердите отклик:"
        kb = review_actions_kb(assignment.id, order.id, order.external_id)
    elif assignment.status == AssignmentStatus.approved:
        # Утверждена, ждёт PM
        kb = approved_kb(order.external_id, order_id=order.id)
    else:
        # sent / in_progress / cancelled / etc — только просмотр
        kb = order_detail_kb(order.id, order.external_id, has_materials=bool(order.materials))

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=kb,
    )


# ---------------------------------------------------------------------------
# 8. Статистика
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "dev:stats")
async def handle_dev_stats(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать статистику системы и разработчика."""
    from src.bot.services.analytics import get_developer_stats, get_system_stats

    await callback.answer()
    user_id = callback.from_user.id  # type: ignore[union-attr]

    async with session_factory() as session:
        sys_stats = await get_system_stats(session)

        member_result = await session.execute(
            select(TeamMember).where(TeamMember.tg_id == user_id)
        )
        member = member_result.scalar_one_or_none()

        if member:
            stats_7d = await get_developer_stats(session, member.id, days=7)
            stats_all = await get_developer_stats(session, member.id, days=None)
        else:
            stats_7d = stats_all = None

    def _fmt_price(val: int | None) -> str:
        return f"{val:,} руб.".replace(",", " ") if val else "—"

    def _fmt_time(val: float | None) -> str:
        return f"{val} ч." if val else "—"

    text = (
        "<b>Статистика</b>\n\n"
        "<b>Система:</b>\n"
        f"  Заявок: {sys_stats['total_orders']} | "
        f"Взято: {sys_stats['total_assigned']} | "
        f"Утверждено: {sys_stats['total_approved']}\n"
        f"  Отправлено клиентам: {sys_stats['total_sent_to_client']}\n"
        f"  Расходы на отклики: {sys_stats['total_response_cost']} руб.\n"
    )

    if stats_7d:
        text += (
            f"\n<b>Я (7 дней):</b>\n"
            f"  Взял: {stats_7d['taken']} | "
            f"Утверждено: {stats_7d['approved']}\n"
            f"  Ср. чек: {_fmt_price(stats_7d['avg_price'])}\n"
            f"  Ср. время взятия: {_fmt_time(stats_7d['avg_time_to_take_hours'])}\n"
        )

    if stats_all:
        text += (
            f"\n<b>Я (всё время):</b>\n"
            f"  Взял: {stats_all['taken']} | "
            f"Утверждено: {stats_all['approved']}\n"
            f"  Ср. чек: {_fmt_price(stats_all['avg_price'])}\n"
        )

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=back_to_dev_kb(),
    )
