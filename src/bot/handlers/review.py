"""Обработчики редактирования отклика в личке разработчика."""
import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.config import Settings
from src.core.models import (
    AiAnalysis,
    AssignmentStatus,
    ManagerResponse,
    Order,
    OrderAssignment,
    OrderStatus,
    TeamMember,
    TeamRole,
)
from src.bot.states import ReviewStates
from src.bot.keyboards.review import approved_kb, cancel_review_kb, review_actions_kb
from aiogram.exceptions import TelegramAPIError
from src.bot.handlers.manager import send_to_manager
from src.bot.services.notification import order_actions_keyboard
from src.ai.context import OrderContext
from src.ai.openrouter import OpenRouterClient
from src.ai.prompts.response import SYSTEM_PROMPT as DEFAULT_RESPONSE_PROMPT
from src.ai.prompts.response import build_response_prompt_v2
from src.ai.prompts.roadmap import SYSTEM_PROMPT as DEFAULT_ROADMAP_PROMPT
from src.ai.prompts.roadmap import build_roadmap_prompt_v2
from src.core.settings_service import get_prompt, get_setting

logger = logging.getLogger(__name__)

router = Router(name="review")

# Часовой пояс Москвы (UTC+3)
MSK = timezone(timedelta(hours=3))


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _now_msk() -> datetime:
    """Текущее время по Москве."""
    return datetime.now(MSK)


def _format_price_range(
    price_min: int | None, price_max: int | None, fallback: str = "не указана",
) -> str:
    """Форматирование диапазона цен."""
    parts: list[str] = []
    if price_min:
        parts.append(f"от {int(price_min):,}".replace(",", " "))
    if price_max:
        parts.append(f"до {int(price_max):,}".replace(",", " "))
    return (" ".join(parts) + " руб.") if parts else fallback


async def _get_assignment_with_order(
    session: AsyncSession,
    assignment_id: int,
) -> OrderAssignment | None:
    """Загрузить назначение вместе с заказом и анализом."""
    result = await session.execute(
        select(OrderAssignment)
        .options(
            selectinload(OrderAssignment.order).selectinload(Order.analyses),
            selectinload(OrderAssignment.developer),
        )
        .where(OrderAssignment.id == assignment_id)
    )
    return result.scalar_one_or_none()


async def _get_assignment_minimal(
    session: AsyncSession,
    assignment_id: int,
) -> OrderAssignment | None:
    """Загрузить назначение с заказом (без анализов и разработчика)."""
    result = await session.execute(
        select(OrderAssignment)
        .options(selectinload(OrderAssignment.order))
        .where(OrderAssignment.id == assignment_id)
    )
    return result.scalar_one_or_none()


def _build_review_summary(
    assignment: OrderAssignment,
    order: Order,
    analysis: AiAnalysis | None = None,
) -> str:
    """Сформировать ПОЛНУЮ карточку отклика (как при первом взятии заявки).

    Включает: выжимку, пожелания, стек, цену, сроки, сложность,
    вопросы, риски, заметки, черновик отклика.
    """
    ctx = OrderContext.from_order_data(order, analysis, assignment)

    stack_str = ", ".join(ctx.effective_stack) if ctx.effective_stack else "—"
    price_display = _format_price_range(ctx.price_min, ctx.price_max, fallback="—")
    response_price_str = f"{order.response_price} руб." if order.response_price else "—"

    text = f"<b>Заявка:</b> {order.external_id} — {order.title}\n\n"

    # AI-выжимка
    if ctx.summary:
        text += f"<b>AI-выжимка:</b>\n{ctx.summary}\n\n"

    # Пожелания заказчика
    if ctx.has_client_requirements:
        text += f"<b>Пожелания заказчика:</b>\n{ctx.client_requirements}\n\n"

    # Основные параметры (с учётом редактирования)
    text += (
        f"<b>Стек:</b> {stack_str}\n"
        f"<b>AI-оценка цены:</b> {price_display}\n"
        f"<b>Цена (можно изменить):</b> {ctx.effective_price or '—'} руб.\n"
        f"<b>Сроки:</b> {ctx.effective_timeline} дн.\n"
        f"<b>Сложность:</b> {ctx.complexity}\n"
        f"<b>Цена отклика:</b> {response_price_str}\n"
    )

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

    # Черновик отклика
    draft = ctx.response_draft or "—"
    text += (
        f"\n<b>Черновик отклика:</b>\n<i>{draft}</i>\n\n"
        "Отредактируйте параметры или утвердите отклик:"
    )

    return text


async def _regenerate_response_draft(
    assignment: OrderAssignment,
    order: Order,
    analysis: AiAnalysis | None,
    session: AsyncSession,
    settings: Settings,
) -> str:
    """Перегенерировать черновик отклика через AI с текущими параметрами."""
    context = OrderContext.from_order_data(order, analysis, assignment)
    # Очищаем старый черновик — иначе AI будет ориентироваться на устаревшие цифры
    context.response_draft = ""

    custom_prompt = await get_prompt(session, "response")
    response_system_prompt = custom_prompt if custom_prompt is not None else DEFAULT_RESPONSE_PROMPT

    style = {
        "tone": await get_setting(session, "manager_style_tone") or "professional",
        "intro": await get_setting(session, "manager_style_intro"),
        "rules": await get_setting(session, "manager_style_rules"),
        "name": await get_setting(session, "manager_profile_name"),
        "signature": await get_setting(session, "manager_profile_signature"),
        "contacts": await get_setting(session, "manager_profile_contacts"),
    }

    async with OpenRouterClient(
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
    ) as ai_client:
        user_prompt = build_response_prompt_v2(context, style=style)
        return await ai_client.complete(response_system_prompt, user_prompt)


async def _update_review_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    assignment: OrderAssignment,
    order: Order,
    analysis: AiAnalysis | None = None,
) -> None:
    """Обновить основное сообщение-карточку отклика (edit_text)."""
    summary = _build_review_summary(assignment, order, analysis)
    try:
        await bot.edit_message_text(
            text=summary,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=review_actions_kb(
                assignment.id, order.id, order.external_id,
            ),
        )
    except TelegramAPIError as e:
        if "message is not modified" not in str(e):
            logger.warning("Не удалось обновить review message: %s", e)


async def _cleanup_prompt(bot: Bot, chat_id: int, data: dict, user_message: Message) -> None:
    """Удалить промежуточные сообщения: подсказку бота и ввод пользователя."""
    # Удаляем подсказку бота
    prompt_msg_id = data.get("prompt_message_id")
    if prompt_msg_id:
        try:
            await bot.delete_message(chat_id, prompt_msg_id)
        except TelegramAPIError:
            pass
    # Удаляем сообщение пользователя
    try:
        await user_message.delete()
    except TelegramAPIError:
        pass


def _parse_timeline_days(text: str) -> str:
    """Распознать сроки из текста и вернуть строку с днями."""
    text_lower = text.lower().strip()

    # «N недель(и)»
    m = re.search(r"(\d+)\s*недел", text_lower)
    if m:
        return str(int(m.group(1)) * 7)

    # «N месяц(а/ев)»
    m = re.search(r"(\d+)\s*месяц", text_lower)
    if m:
        return str(int(m.group(1)) * 30)

    # Голое число или «N дн/день/дней»
    m = re.search(r"(\d+)", text_lower)
    if m:
        return m.group(1)

    return text.strip()


# ---------------------------------------------------------------------------
# Callback: отмена редактирования
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("cancel_review:"))
async def handle_cancel_review(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Отменить текущее редактирование — просто удалить промежуточное сообщение."""
    await state.clear()
    await callback.answer()

    # Удаляем промежуточное сообщение с вопросом
    try:
        await callback.message.delete()  # type: ignore[union-attr]
    except TelegramAPIError:
        pass


# ---------------------------------------------------------------------------
# Callback: noop (информационные кнопки)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("noop:"))
async def handle_noop(callback: CallbackQuery) -> None:
    """Информационная кнопка — ничего не делает."""
    await callback.answer()


# ---------------------------------------------------------------------------
# Callback: начало редактирования
# ---------------------------------------------------------------------------

async def _start_edit(
    callback: CallbackQuery,
    state: FSMContext,
    fsm_state: str,
    prompt_text: str,
) -> None:
    """Общая логика начала редактирования: сохранить message_id, установить FSM."""
    await callback.answer()
    assignment_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    # Очищаем предыдущее FSM состояние если есть
    await state.clear()
    await state.update_data(
        assignment_id=assignment_id,
        review_message_id=callback.message.message_id,  # type: ignore[union-attr]
        review_chat_id=callback.message.chat.id,  # type: ignore[union-attr]
    )
    await state.set_state(fsm_state)
    prompt_msg = await callback.message.answer(  # type: ignore[union-attr]
        prompt_text,
        reply_markup=cancel_review_kb(assignment_id),
    )
    await state.update_data(prompt_message_id=prompt_msg.message_id)


@router.callback_query(F.data.startswith("edit_price:"))
async def start_edit_price(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование цены."""
    await _start_edit(
        callback, state, ReviewStates.editing_price,
        "Введите итоговую цену (число в рублях):",
    )


@router.callback_query(F.data.startswith("edit_timeline:"))
async def start_edit_timeline(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование сроков."""
    await _start_edit(
        callback, state, ReviewStates.editing_timeline,
        "Введите сроки (например: 5 дней, 2 недели, 21 день):",
    )


@router.callback_query(F.data.startswith("edit_stack:"))
async def start_edit_stack(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование стека."""
    await _start_edit(
        callback, state, ReviewStates.editing_stack,
        "Введите стек технологий через запятую (например: Python, FastAPI, PostgreSQL):",
    )


@router.callback_query(F.data.startswith("edit_custom:"))
async def start_edit_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать ввод заметки для PM."""
    await _start_edit(
        callback, state, ReviewStates.editing_custom,
        "Введите заметку для менеджера (будет отправлена PM вместе с откликом):",
    )


@router.callback_query(F.data.startswith("edit_response:"))
async def start_edit_response(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать ручное редактирование текста отклика."""
    await _start_edit(
        callback, state, ReviewStates.editing_response_draft,
        "Введите новый текст отклика (полностью заменит черновик):",
    )


# ---------------------------------------------------------------------------
# FSM: обработка текстовых сообщений (ввод значений)
# ---------------------------------------------------------------------------

@router.message(ReviewStates.editing_price)
async def process_edit_price(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """Сохранить новую цену и перегенерировать отклик."""
    data = await state.get_data()
    assignment_id: int = data["assignment_id"]
    review_message_id: int = data.get("review_message_id", 0)
    review_chat_id: int = data.get("review_chat_id", message.chat.id)

    raw = message.text.strip().replace(" ", "").replace("\u00a0", "") if message.text else ""
    if not raw.isdigit():
        await message.answer("Пожалуйста, введите число (например: 50000):")
        return

    price = int(raw)

    async with session_factory() as session:
        assignment = await _get_assignment_with_order(session, assignment_id)
        if not assignment:
            await message.answer("Назначение не найдено.")
            await state.clear()
            return

        assignment.price_final = price
        order = assignment.order
        analysis = order.analyses[0] if order.analyses else None

        # Перегенерируем черновик отклика
        try:
            new_draft = await _regenerate_response_draft(
                assignment, order, analysis, session, settings,
            )
            if analysis:
                analysis.response_draft = new_draft
        except Exception:
            logger.exception("Ошибка перегенерации отклика при изменении цены")

        await session.commit()

    await state.clear()
    await _cleanup_prompt(bot, message.chat.id, data, message)

    if review_message_id:
        await _update_review_message(
            bot, review_chat_id, review_message_id, assignment, order, analysis,
        )


@router.message(ReviewStates.editing_timeline)
async def process_edit_timeline(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """Сохранить новые сроки и перегенерировать отклик."""
    data = await state.get_data()
    assignment_id: int = data["assignment_id"]
    review_message_id: int = data.get("review_message_id", 0)
    review_chat_id: int = data.get("review_chat_id", message.chat.id)
    timeline_raw = message.text.strip() if message.text else ""

    if not timeline_raw:
        await message.answer("Пожалуйста, введите сроки:")
        return

    timeline = _parse_timeline_days(timeline_raw)

    async with session_factory() as session:
        assignment = await _get_assignment_with_order(session, assignment_id)
        if not assignment:
            await message.answer("Назначение не найдено.")
            await state.clear()
            return

        assignment.timeline_final = timeline
        order = assignment.order
        analysis = order.analyses[0] if order.analyses else None

        try:
            new_draft = await _regenerate_response_draft(
                assignment, order, analysis, session, settings,
            )
            if analysis:
                analysis.response_draft = new_draft
        except Exception:
            logger.exception("Ошибка перегенерации отклика при изменении сроков")

        await session.commit()

    await state.clear()
    await _cleanup_prompt(bot, message.chat.id, data, message)

    if review_message_id:
        await _update_review_message(
            bot, review_chat_id, review_message_id, assignment, order, analysis,
        )


@router.message(ReviewStates.editing_stack)
async def process_edit_stack(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """Сохранить новый стек и перегенерировать отклик."""
    data = await state.get_data()
    assignment_id: int = data["assignment_id"]
    review_message_id: int = data.get("review_message_id", 0)
    review_chat_id: int = data.get("review_chat_id", message.chat.id)
    raw = message.text.strip() if message.text else ""

    if not raw:
        await message.answer("Пожалуйста, введите стек технологий:")
        return

    stack = [s.strip() for s in raw.split(",") if s.strip()]

    async with session_factory() as session:
        assignment = await _get_assignment_with_order(session, assignment_id)
        if not assignment:
            await message.answer("Назначение не найдено.")
            await state.clear()
            return

        assignment.stack_final = stack
        order = assignment.order
        analysis = order.analyses[0] if order.analyses else None

        try:
            new_draft = await _regenerate_response_draft(
                assignment, order, analysis, session, settings,
            )
            if analysis:
                analysis.response_draft = new_draft
        except Exception:
            logger.exception("Ошибка перегенерации отклика при изменении стека")

        await session.commit()

    await state.clear()
    await _cleanup_prompt(bot, message.chat.id, data, message)

    if review_message_id:
        await _update_review_message(
            bot, review_chat_id, review_message_id, assignment, order, analysis,
        )


@router.message(ReviewStates.editing_custom)
async def process_edit_custom(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """Сохранить заметку для PM (не влияет на отклик)."""
    data = await state.get_data()
    assignment_id: int = data["assignment_id"]
    review_message_id: int = data.get("review_message_id", 0)
    review_chat_id: int = data.get("review_chat_id", message.chat.id)
    notes = message.text.strip() if message.text else ""

    if not notes:
        await message.answer("Пожалуйста, введите заметку:")
        return

    async with session_factory() as session:
        assignment = await _get_assignment_with_order(session, assignment_id)
        if not assignment:
            await message.answer("Назначение не найдено.")
            await state.clear()
            return

        assignment.custom_notes = notes
        order = assignment.order
        analysis = order.analyses[0] if order.analyses else None
        await session.commit()

    await state.clear()
    await _cleanup_prompt(bot, message.chat.id, data, message)

    if review_message_id:
        await _update_review_message(
            bot, review_chat_id, review_message_id, assignment, order, analysis,
        )


@router.message(ReviewStates.editing_response_draft)
async def process_edit_response_draft(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """Сохранить ручное редактирование текста отклика."""
    data = await state.get_data()
    assignment_id: int = data["assignment_id"]
    review_message_id: int = data.get("review_message_id", 0)
    review_chat_id: int = data.get("review_chat_id", message.chat.id)
    new_text = message.text.strip() if message.text else ""

    if not new_text:
        await message.answer("Пожалуйста, введите текст отклика:")
        return

    async with session_factory() as session:
        assignment = await _get_assignment_with_order(session, assignment_id)
        if not assignment:
            await message.answer("Назначение не найдено.")
            await state.clear()
            return

        order = assignment.order
        analysis = order.analyses[0] if order.analyses else None

        # Сохраняем ручной текст в response_draft анализа
        if analysis:
            analysis.response_draft = new_text
        await session.commit()

    await state.clear()
    await _cleanup_prompt(bot, message.chat.id, data, message)

    if review_message_id:
        await _update_review_message(
            bot, review_chat_id, review_message_id, assignment, order, analysis,
        )


# ---------------------------------------------------------------------------
# Callback: утверждение отклика
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("approve:"))
async def handle_approve(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """Утвердить отклик: использует текущий черновик или генерирует через AI."""
    assignment_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        assignment = await _get_assignment_with_order(session, assignment_id)
        if not assignment:
            await callback.message.answer("Назначение не найдено.")  # type: ignore[union-attr]
            return

        order = assignment.order
        analysis = order.analyses[0] if order.analyses else None

        # Если есть готовый черновик (ручной или перегенерированный) — используем его
        existing_draft = analysis.response_draft if analysis else None
        if existing_draft and existing_draft.strip():
            await callback.answer("Отправляем отклик...")
            response_text = existing_draft.strip()
        else:
            # Нет черновика — генерируем через AI
            await callback.answer("Генерируем отклик...")
            context = OrderContext.from_order_data(order, analysis, assignment)

            custom_prompt = await get_prompt(session, "response")
            response_system_prompt = custom_prompt if custom_prompt is not None else DEFAULT_RESPONSE_PROMPT

            style = {
                "tone": await get_setting(session, "manager_style_tone") or "professional",
                "intro": await get_setting(session, "manager_style_intro"),
                "rules": await get_setting(session, "manager_style_rules"),
                "name": await get_setting(session, "manager_profile_name"),
                "signature": await get_setting(session, "manager_profile_signature"),
                "contacts": await get_setting(session, "manager_profile_contacts"),
            }

            async with OpenRouterClient(
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
            ) as ai_client:
                user_prompt = build_response_prompt_v2(context, style=style)
                response_text = await ai_client.complete(response_system_prompt, user_prompt)

        # Сохраняем ManagerResponse
        manager_response = ManagerResponse(
            assignment_id=assignment_id,
            response_text=response_text,
        )
        session.add(manager_response)

        assignment.status = AssignmentStatus.approved
        assignment.approved_at = datetime.utcnow()
        order.status = OrderStatus.completed
        await session.commit()
        await session.refresh(manager_response)

        # Сохраняем скалярные значения до закрытия сессии
        order_id = order.id
        order_external_id = order.external_id
        order_title = order.title
        custom_notes = assignment.custom_notes
        stack_final = assignment.stack_final
        price_final = assignment.price_final
        timeline_final = assignment.timeline_final

        # Ищем менеджера для кнопки «Связаться с PM»
        mgr_result = await session.execute(
            select(TeamMember).where(
                TeamMember.role == TeamRole.manager,
                TeamMember.is_active.is_(True),
            )
        )
        manager = mgr_result.scalar_one_or_none()
        manager_username = manager.tg_username if manager else None

        logger.info(
            "Отклик по заявке #%s утверждён разработчиком (assignment_id=%s)",
            order_external_id, assignment_id,
        )

    # Обновляем основное сообщение — оставляем полную карточку, меняем кнопки
    stack_str = ", ".join(stack_final) if stack_final else "—"
    now_msk = _now_msk().strftime("%H:%M %d.%m.%Y")
    approved_text = (
        f"<b>Отклик утверждён</b> | {order_external_id}\n"
        f"<i>{now_msk} МСК</i>\n\n"
        f"<b>Заявка:</b> {order_external_id} — {order_title}\n\n"
        f"<b>Цена:</b> {price_final or '—'} руб.\n"
        f"<b>Сроки:</b> {timeline_final or '—'} дн.\n"
        f"<b>Стек:</b> {stack_str}\n"
    )

    if custom_notes:
        approved_text += f"<b>Заметка для PM:</b> {custom_notes}\n"

    approved_text += (
        f"\n<b>Сгенерированный отклик:</b>\n{response_text}\n\n"
        "Отклик отправлен менеджеру."
    )

    try:
        await callback.message.edit_text(  # type: ignore[union-attr]
            text=approved_text,
            reply_markup=approved_kb(order_external_id, manager_username, order_id),
        )
    except TelegramAPIError as e:
        logger.warning("Не удалось обновить сообщение после утверждения: %s", e)

    # Отправляем менеджеру (с заметкой)
    await send_to_manager(
        bot=bot,
        settings=settings,
        session_factory=session_factory,
        assignment_id=assignment_id,
        response_text=response_text,
    )


# ---------------------------------------------------------------------------
# Callback: Pre Roadmap
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("roadmap:"))
async def handle_roadmap(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Сгенерировать Pre Roadmap проекта через AI."""
    await callback.answer("Генерируем Pre Roadmap...")
    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        order_result = await session.execute(
            select(Order)
            .options(
                selectinload(Order.analyses),
                selectinload(Order.assignments),
            )
            .where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()

        if not order:
            await callback.message.answer("Заказ не найден.")  # type: ignore[union-attr]
            return

        analysis = order.analyses[0] if order.analyses else None

        # Ищем активный assignment (в статусе editing или pending)
        assignment = None
        for a in order.assignments:
            if a.status in (AssignmentStatus.editing, AssignmentStatus.pending):
                assignment = a
                break

        # Собираем полный контекст
        context = OrderContext.from_order_data(order, analysis, assignment)

        # Загружаем промпт из DB (fallback на файловый)
        custom_prompt = await get_prompt(session, "roadmap")
        roadmap_system_prompt = custom_prompt if custom_prompt is not None else DEFAULT_ROADMAP_PROMPT

        # Генерируем roadmap с полным контекстом
        async with OpenRouterClient(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
        ) as ai_client:
            user_prompt = build_roadmap_prompt_v2(context)
            roadmap_text = await ai_client.complete(roadmap_system_prompt, user_prompt)

        # Сохраняем roadmap_text в assignment
        if assignment:
            assignment.roadmap_text = roadmap_text
            await session.commit()

        order_external_id = order.external_id

    await callback.message.reply(  # type: ignore[union-attr]
        f"<b>Pre Roadmap | {order_external_id}</b>\n\n{roadmap_text}",
    )


# ---------------------------------------------------------------------------
# Callback: отказ от заявки
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("reject_order:"))
async def handle_reject_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    bot: Bot,
) -> None:
    """Разработчик отказывается от заявки: удаляем assignment, освобождаем заявку в группе."""
    await callback.answer()
    assignment_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        assignment = await _get_assignment_minimal(session, assignment_id)
        if not assignment:
            await callback.message.answer("Назначение не найдено.")  # type: ignore[union-attr]
            return

        order = assignment.order
        group_message_id = assignment.group_message_id
        order_external_id = order.external_id
        order_id = order.id

        # Удаляем assignment из БД
        await session.delete(assignment)
        # Возвращаем статус заказа — можно снова брать
        order.status = OrderStatus.reviewed
        await session.commit()

        logger.info(
            "Разработчик отказался от заявки #%s (assignment_id=%s)",
            order_external_id, assignment_id,
        )

    # Обновляем сообщение в личке разработчика
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"Вы отказались от заявки <b>{order_external_id}</b>.\n"
        "Заявка снова доступна для взятия в группе.",
        reply_markup=None,
    )

    # Обновляем сообщение в группе: возвращаем кнопки «Взять / Пропустить»
    if group_message_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=settings.group_chat_id,
                message_id=group_message_id,
                reply_markup=order_actions_keyboard(order_id, order_external_id),
            )
        except TelegramAPIError:
            logger.warning(
                "Не удалось обновить сообщение в группе для заявки #%s",
                order_external_id,
            )
