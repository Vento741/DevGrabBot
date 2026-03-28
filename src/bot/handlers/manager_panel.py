"""Обработчики панели менеджера (CP-6.1, CP-6.4–CP-6.12)."""
import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.config import Settings
from src.core.redis import RedisClient
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
from src.core.settings_service import get_setting, set_setting
from src.bot.states import ManagerPanelStates
from src.bot.keyboards.manager_panel import (
    back_to_manager_kb,
    cancel_mgr_kb,
    developer_detail_kb,
    developers_list_kb,
    manager_main_menu_kb,
    orders_status_kb,
    profile_settings_kb,
    response_actions_kb,
    responses_filter_kb,
    style_settings_kb,
)
from src.ai.context import OrderContext
from src.ai.openrouter import OpenRouterClient
from src.ai.prompts.response import SYSTEM_PROMPT as DEFAULT_RESPONSE_PROMPT
from src.ai.prompts.response import build_response_prompt_v2
from src.core.settings_service import get_prompt

logger = logging.getLogger(__name__)

router = Router(name="manager_panel")

# ---------------------------------------------------------------------------
# Вспомогательные константы
# ---------------------------------------------------------------------------

_STATUS_LABEL: dict[str, str] = {
    "new": "новая",
    "analyzing": "анализируется",
    "reviewed": "проверена",
    "assigned": "назначена",
    "completed": "завершена",
    "skipped": "пропущена",
}

_ASSIGNMENT_STATUS_LABEL: dict[str, str] = {
    "pending": "ожидает",
    "editing": "редактируется",
    "approved": "утверждён",
    "sent": "отправлен",
    "in_progress": "в работе",
    "cancelled": "отменён",
    "rejected": "отклонён",
    "reassigned": "переназначен",
}


# ---------------------------------------------------------------------------
# Главное меню (CP-6.1)
# ---------------------------------------------------------------------------


@router.message(Command("manager"))
async def cmd_manager(message: Message, state: FSMContext, redis_client: RedisClient) -> None:
    """Команда /manager — открывает панель менеджера."""
    await state.clear()
    is_paused = await redis_client.is_parser_paused()
    await message.answer(
        "<b>Панель менеджера</b>\n\nВыберите раздел:",
        reply_markup=manager_main_menu_kb(is_paused=is_paused),
    )
    logger.info("Менеджер %s открыл панель", message.from_user.id if message.from_user else "?")


@router.callback_query(F.data == "mgr:back")
async def handle_mgr_back(callback: CallbackQuery, state: FSMContext, redis_client: RedisClient) -> None:
    """Вернуться в главное меню панели менеджера."""
    await callback.answer()
    await state.clear()
    is_paused = await redis_client.is_parser_paused()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Панель менеджера</b>\n\nВыберите раздел:",
        reply_markup=manager_main_menu_kb(is_paused=is_paused),
    )


@router.callback_query(F.data == "mgr:toggle_available")
async def handle_mgr_toggle_available(
    callback: CallbackQuery,
    redis_client: RedisClient,
    settings: Settings,
) -> None:
    """Переключить доступность PM (пауза/возобновление парсера)."""
    is_paused = await redis_client.is_parser_paused()

    if is_paused:
        await redis_client.set_parser_resumed()
        new_paused = False
        group_msg = "\u25b6\ufe0f PM готов отправлять отклики, парсер запущен \U0001f680"
        alert_text = "Парсер запущен"
    else:
        await redis_client.set_parser_paused()
        new_paused = True
        group_msg = "\u23f8 PM не готов отправлять отклики, парсер остановлен \u2615"
        alert_text = "Парсер приостановлен"

    # Сообщение в группу
    try:
        await callback.bot.send_message(  # type: ignore[union-attr]
            chat_id=settings.group_chat_id,
            text=group_msg,
        )
    except Exception:
        logger.warning("Не удалось отправить сообщение о паузе в группу")

    await callback.answer(alert_text, show_alert=True)
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Панель менеджера</b>\n\nВыберите раздел:",
        reply_markup=manager_main_menu_kb(is_paused=new_paused),
    )
    logger.info("PM %s переключил доступность: paused=%s", callback.from_user.id, new_paused)


# ---------------------------------------------------------------------------
# Входящие отклики (CP-6.4)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "mgr:responses")
async def handle_mgr_responses(callback: CallbackQuery, state: FSMContext) -> None:
    """Открыть раздел входящих откликов с фильтром."""
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Входящие отклики</b>\n\nВыберите фильтр:",
        reply_markup=responses_filter_kb(),
    )


async def _get_responses_list(
    session: AsyncSession,
    filter_sent: bool | None,
) -> list[ManagerResponse]:
    """Загрузить отклики с учётом фильтра."""
    query = (
        select(ManagerResponse)
        .options(
            selectinload(ManagerResponse.assignment).selectinload(
                OrderAssignment.order
            ),
            selectinload(ManagerResponse.assignment).selectinload(
                OrderAssignment.developer
            ),
        )
        .order_by(ManagerResponse.sent_at.desc())
    )
    if filter_sent is not None:
        query = query.where(ManagerResponse.sent_to_client == filter_sent)

    result = await session.execute(query)
    return list(result.scalars().all())


def _format_response_item(resp: ManagerResponse) -> str:
    """Форматировать одну запись в списке откликов."""
    assignment = resp.assignment
    order = assignment.order if assignment else None
    dev = assignment.developer if assignment else None

    order_id = order.external_id if order else "—"
    order_title = order.title if order else "—"
    dev_name = dev.name if dev else "—"
    status_label = "отправлен клиенту" if resp.sent_to_client else "ожидает отправки"

    return (
        f"<b>Отклик</b> | #{order_id}\n"
        f"Заявка: {order_title}\n"
        f"Исполнитель: {dev_name}\n"
        f"Статус: {status_label}"
    )


@router.callback_query(F.data.in_({"resp:new", "resp:sent", "resp:all"}))
async def handle_responses_filter(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать список откликов по фильтру."""
    await callback.answer()

    filter_map = {"resp:new": False, "resp:sent": True, "resp:all": None}
    filter_sent = filter_map[callback.data]  # type: ignore[index]

    async with session_factory() as session:
        responses = await _get_responses_list(session, filter_sent)

    if not responses:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Откликов не найдено.",
            reply_markup=responses_filter_kb(),
        )
        return

    # Формируем список (не более 10 записей для читаемости)
    items: list[str] = []
    for resp in responses[:10]:
        items.append(_format_response_item(resp))

    header_map = {
        "resp:new": "Новые отклики (ожидают отправки)",
        "resp:sent": "Отправленные отклики",
        "resp:all": "Все отклики",
    }
    header = header_map[callback.data]  # type: ignore[index]
    total_note = f"\n\nПоказано: {min(len(responses), 10)} из {len(responses)}" if len(responses) > 10 else ""

    # Строим inline-кнопки для каждого отклика
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows: list[list[InlineKeyboardButton]] = []
    for resp in responses[:10]:
        assignment = resp.assignment
        order = assignment.order if assignment else None
        label = f"Подробнее → #{order.external_id if order else resp.id}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"resp:detail:{resp.id}")]
        )
    rows.append([InlineKeyboardButton(text="<- Назад", callback_data="mgr:responses")])

    text = f"<b>{header}</b>{total_note}\n\n" + "\n\n".join(items)

    # Телеграм лимит: разбиваем при необходимости
    if len(text) > 4096:
        text = text[:4090] + "..."

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("resp:detail:"))
async def handle_response_detail(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать полный текст отклика с кнопками действий."""
    await callback.answer()
    response_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(ManagerResponse)
            .options(
                selectinload(ManagerResponse.assignment).selectinload(
                    OrderAssignment.order
                ),
                selectinload(ManagerResponse.assignment).selectinload(
                    OrderAssignment.developer
                ),
            )
            .where(ManagerResponse.id == response_id)
        )
        resp = result.scalar_one_or_none()

    if not resp:
        await callback.message.answer("Отклик не найден.")  # type: ignore[union-attr]
        return

    assignment = resp.assignment
    order = assignment.order if assignment else None
    dev = assignment.developer if assignment else None

    order_id = order.external_id if order else "—"
    order_title = order.title if order else "—"
    dev_name = dev.name if dev else "—"
    status_label = "отправлен клиенту" if resp.sent_to_client else "ожидает отправки"

    # Показываем edited_text если есть
    display_text = resp.edited_text if resp.edited_text else resp.response_text
    assignment_id = assignment.id if assignment else 0

    text = (
        f"<b>Отклик | #{order_id}</b>\n"
        f"Заявка: {order_title}\n"
        f"Исполнитель: {dev_name}\n"
        f"Статус: {status_label}\n\n"
        f"<b>Текст отклика:</b>\n{display_text}"
    )

    if len(text) > 4096:
        text = text[:4090] + "..."

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=response_actions_kb(response_id, assignment_id),
    )


# ---------------------------------------------------------------------------
# Расширенные действия с откликом (CP-6.10)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("resp:edit:"))
async def handle_resp_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование текста отклика."""
    await callback.answer()
    response_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]
    await state.update_data(response_id=response_id)
    await state.set_state(ManagerPanelStates.editing_response_text)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите новый текст отклика:",
        reply_markup=cancel_mgr_kb("mgr:responses"),
    )


@router.message(ManagerPanelStates.editing_response_text)
async def process_response_text(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить отредактированный текст отклика."""
    data = await state.get_data()
    response_id: int = data["response_id"]
    new_text = message.text.strip() if message.text else ""

    if not new_text:
        await message.answer("Текст не может быть пустым. Введите текст отклика:")
        return

    async with session_factory() as session:
        result = await session.execute(
            select(ManagerResponse).where(ManagerResponse.id == response_id)
        )
        resp = result.scalar_one_or_none()

        if not resp:
            await message.answer("Отклик не найден.")
            await state.clear()
            return

        resp.edited_text = new_text
        await session.commit()

    await state.clear()
    await message.answer(
        "Текст отклика сохранён.",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Менеджер отредактировал отклик response_id=%s", response_id)


@router.callback_query(F.data.startswith("resp:mark_sent:"))
async def handle_resp_mark_sent(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Отметить отклик как отправленный клиенту."""
    await callback.answer()
    response_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(ManagerResponse)
            .options(
                selectinload(ManagerResponse.assignment)
                .selectinload(OrderAssignment.order)
            )
            .where(ManagerResponse.id == response_id)
        )
        resp = result.scalar_one_or_none()

        if not resp:
            await callback.message.answer("Отклик не найден.")  # type: ignore[union-attr]
            return

        resp.sent_to_client = True
        resp.sent_to_client_at = datetime.utcnow()
        await session.commit()

        # Данные для уведомления в группу
        order = resp.assignment.order
        external_id = order.external_id
        response_price = order.response_price

    # Уведомление в группу
    price_str = f" ({response_price} руб.)" if response_price else ""
    await callback.bot.send_message(  # type: ignore[union-attr]
        chat_id=settings.group_chat_id,
        text=f"Отклик по заявке <b>{external_id}</b> отправлен клиенту{price_str}",
    )

    await callback.message.answer(  # type: ignore[union-attr]
        "Отклик отмечен как отправленный клиенту.",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Отклик response_id=%s отмечен отправленным", response_id)


@router.callback_query(F.data.startswith("resp:regen:"))
async def handle_resp_regen(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Перегенерировать отклик через AI с учётом стиля менеджера."""
    await callback.answer("Перегенерируем отклик...")
    assignment_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    async with session_factory() as session:
        # Загружаем назначение с заказом и анализом
        result = await session.execute(
            select(OrderAssignment)
            .options(
                selectinload(OrderAssignment.order).selectinload(Order.analyses),
                selectinload(OrderAssignment.developer),
            )
            .where(OrderAssignment.id == assignment_id)
        )
        assignment = result.scalar_one_or_none()

        if not assignment:
            await callback.message.answer("Назначение не найдено.")  # type: ignore[union-attr]
            return

        order = assignment.order
        analysis = order.analyses[0] if order.analyses else None

        # Собираем контекст
        context = OrderContext.from_order_data(order, analysis, assignment)

        # Загружаем стиль + профиль менеджера из settings
        style = {
            "tone": await get_setting(session, "manager_style_tone") or "professional",
            "intro": await get_setting(session, "manager_style_intro"),
            "rules": await get_setting(session, "manager_style_rules"),
            "name": await get_setting(session, "manager_profile_name"),
            "signature": await get_setting(session, "manager_profile_signature"),
            "contacts": await get_setting(session, "manager_profile_contacts"),
        }

        # Загружаем промпт из DB (fallback на файловый)
        custom_prompt = await get_prompt(session, "response")
        response_system_prompt = custom_prompt if custom_prompt is not None else DEFAULT_RESPONSE_PROMPT

        # Если заданы правила стиля — добавляем к системному промпту
        style_additions: list[str] = []
        if style["intro"]:
            style_additions.append(f"Вступление: {style['intro']}")
        if style["rules"]:
            style_additions.append(f"Дополнительные правила: {style['rules']}")
        if style["tone"] != "professional":
            style_additions.append(f"Тон общения: {style['tone']}")

        if style_additions:
            response_system_prompt = response_system_prompt + "\n\n" + "\n".join(style_additions)

        # Генерация через AI
        async with OpenRouterClient(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
        ) as ai_client:
            user_prompt = build_response_prompt_v2(context)
            new_response_text = await ai_client.complete(response_system_prompt, user_prompt)

        # Ищем или создаём ManagerResponse
        resp_result = await session.execute(
            select(ManagerResponse).where(ManagerResponse.assignment_id == assignment_id)
        )
        resp = resp_result.scalar_one_or_none()

        if resp:
            resp.edited_text = new_response_text
        else:
            resp = ManagerResponse(
                assignment_id=assignment_id,
                response_text=new_response_text,
            )
            session.add(resp)

        await session.commit()

        order_external_id = order.external_id

    await callback.message.answer(  # type: ignore[union-attr]
        f"<b>Перегенерированный отклик | {order_external_id}</b>\n\n{new_response_text}",
        reply_markup=back_to_manager_kb(),
    )
    logger.info(
        "Перегенерирован отклик для заявки %s (assignment_id=%s)",
        order_external_id, assignment_id,
    )


# ---------------------------------------------------------------------------
# Стиль откликов (CP-6.5)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "mgr:style")
async def handle_mgr_style(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    """Показать текущий стиль откликов."""
    await state.clear()
    await callback.answer()

    async with session_factory() as session:
        tone = await get_setting(session, "manager_style_tone") or "professional"
        intro = await get_setting(session, "manager_style_intro") or "—"
        rules = await get_setting(session, "manager_style_rules") or "—"

    # Обрезаем длинные значения для отображения
    if len(intro) > 200:
        intro = intro[:197] + "..."
    if len(rules) > 200:
        rules = rules[:197] + "..."

    text = (
        "<b>Стиль откликов</b>\n\n"
        f"Тон: {tone}\n"
        f"Вступление: {intro}\n"
        f"Правила: {rules}"
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=style_settings_kb(),
    )


@router.callback_query(F.data == "style:tone")
async def handle_style_tone(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование тона общения."""
    await callback.answer()
    await state.set_state(ManagerPanelStates.editing_style_tone)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите тон общения (например: professional, friendly, casual):",
        reply_markup=cancel_mgr_kb("mgr:style"),
    )


@router.message(ManagerPanelStates.editing_style_tone)
async def process_style_tone(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить тон общения."""
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Значение не может быть пустым:")
        return

    async with session_factory() as session:
        await set_setting(session, "manager_style_tone", value)

    await state.clear()
    await message.answer(
        f"Тон обновлён: <b>{value}</b>",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Обновлён стиль manager_style_tone=%s", value)


@router.callback_query(F.data == "style:intro")
async def handle_style_intro(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование вступительного текста."""
    await callback.answer()
    await state.set_state(ManagerPanelStates.editing_style_intro)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите вступительный текст команды (используется в начале откликов):",
        reply_markup=cancel_mgr_kb("mgr:style"),
    )


@router.message(ManagerPanelStates.editing_style_intro)
async def process_style_intro(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить вступительный текст."""
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Значение не может быть пустым:")
        return

    async with session_factory() as session:
        await set_setting(session, "manager_style_intro", value)

    await state.clear()
    await message.answer(
        "Вступление сохранено.",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Обновлён стиль manager_style_intro")


@router.callback_query(F.data == "style:rules")
async def handle_style_rules(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование правил генерации."""
    await callback.answer()
    await state.set_state(ManagerPanelStates.editing_style_rules)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите правила генерации откликов (дополнительные инструкции для AI):",
        reply_markup=cancel_mgr_kb("mgr:style"),
    )


@router.message(ManagerPanelStates.editing_style_rules)
async def process_style_rules(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить правила генерации."""
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Значение не может быть пустым:")
        return

    async with session_factory() as session:
        await set_setting(session, "manager_style_rules", value)

    await state.clear()
    await message.answer(
        "Правила генерации сохранены.",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Обновлён стиль manager_style_rules")


# ---------------------------------------------------------------------------
# Профиль менеджера (CP-6.6)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "mgr:profile")
async def handle_mgr_profile(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    """Показать текущий профиль менеджера."""
    await state.clear()
    await callback.answer()

    async with session_factory() as session:
        name = await get_setting(session, "manager_profile_name") or "—"
        signature = await get_setting(session, "manager_profile_signature") or "—"
        contacts = await get_setting(session, "manager_profile_contacts") or "—"

    text = (
        "<b>Профиль менеджера</b>\n\n"
        f"Имя для откликов: {name}\n"
        f"Подпись: {signature}\n"
        f"Контакты: {contacts}"
    )
    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=profile_settings_kb(),
    )


@router.callback_query(F.data == "profile:name")
async def handle_profile_name(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование имени для откликов."""
    await callback.answer()
    await state.set_state(ManagerPanelStates.editing_profile_name)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите имя, которое будет использоваться в откликах:",
        reply_markup=cancel_mgr_kb("mgr:profile"),
    )


@router.message(ManagerPanelStates.editing_profile_name)
async def process_profile_name(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить имя профиля."""
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Значение не может быть пустым:")
        return

    async with session_factory() as session:
        await set_setting(session, "manager_profile_name", value)

    await state.clear()
    await message.answer(
        f"Имя обновлено: <b>{value}</b>",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Обновлён профиль manager_profile_name=%s", value)


@router.callback_query(F.data == "profile:signature")
async def handle_profile_signature(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование подписи."""
    await callback.answer()
    await state.set_state(ManagerPanelStates.editing_profile_signature)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите подпись для откликов:",
        reply_markup=cancel_mgr_kb("mgr:profile"),
    )


@router.message(ManagerPanelStates.editing_profile_signature)
async def process_profile_signature(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить подпись."""
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Значение не может быть пустым:")
        return

    async with session_factory() as session:
        await set_setting(session, "manager_profile_signature", value)

    await state.clear()
    await message.answer(
        "Подпись сохранена.",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Обновлён профиль manager_profile_signature")


@router.callback_query(F.data == "profile:contacts")
async def handle_profile_contacts(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать редактирование контактных данных."""
    await callback.answer()
    await state.set_state(ManagerPanelStates.editing_profile_contacts)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите контактные данные (телефон, мессенджер и т.д.):",
        reply_markup=cancel_mgr_kb("mgr:profile"),
    )


@router.message(ManagerPanelStates.editing_profile_contacts)
async def process_profile_contacts(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранить контактные данные."""
    value = message.text.strip() if message.text else ""
    if not value:
        await message.answer("Значение не может быть пустым:")
        return

    async with session_factory() as session:
        await set_setting(session, "manager_profile_contacts", value)

    await state.clear()
    await message.answer(
        "Контактные данные сохранены.",
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Обновлён профиль manager_profile_contacts")


# ---------------------------------------------------------------------------
# Заявки (CP-6.7)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "mgr:orders")
async def handle_mgr_orders(callback: CallbackQuery, state: FSMContext) -> None:
    """Открыть раздел заявок с фильтром по статусу."""
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Заявки</b>\n\nВыберите статус:",
        reply_markup=orders_status_kb(),
    )


async def _load_orders_by_status(
    session: AsyncSession,
    statuses: list[OrderStatus],
) -> list[Order]:
    """Загрузить заявки по списку статусов с назначениями и разработчиками."""
    query = (
        select(Order)
        .options(
            selectinload(Order.assignments).selectinload(OrderAssignment.developer)
        )
        .where(Order.status.in_(statuses))
        .order_by(Order.created_at.desc())
        .limit(15)
    )
    result = await session.execute(query)
    return list(result.scalars().all())


def _format_order_item(order: Order) -> str:
    """Форматировать заявку для списка менеджера."""
    status_label = _STATUS_LABEL.get(order.status.value, order.status.value)

    # Ищем активного исполнителя
    dev_name = "—"
    price_str = "—"
    for assignment in order.assignments:
        if assignment.status in (
            AssignmentStatus.editing,
            AssignmentStatus.pending,
            AssignmentStatus.approved,
            AssignmentStatus.sent,
        ):
            if assignment.developer:
                dev_name = assignment.developer.name
            if assignment.price_final:
                price_str = f"{assignment.price_final:,} руб.".replace(",", " ")
            break

    return (
        f"<b>Заявка</b> | #{order.external_id}\n"
        f"Название: {order.title}\n"
        f"Статус: {status_label}\n"
        f"Исполнитель: {dev_name}\n"
        f"Цена: {price_str}"
    )


async def _send_orders_list(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    statuses: list[OrderStatus],
    section_title: str,
) -> None:
    """Загрузить и отобразить список заявок."""
    async with session_factory() as session:
        orders = await _load_orders_by_status(session, statuses)

    if not orders:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"<b>{section_title}</b>\n\nЗаявок не найдено.",
            reply_markup=orders_status_kb(),
        )
        return

    items = [_format_order_item(o) for o in orders]
    text = f"<b>{section_title}</b>\n\n" + "\n\n".join(items)

    if len(text) > 4096:
        text = text[:4090] + "..."

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=orders_status_kb(),
    )


@router.callback_query(F.data == "morders:new")
async def handle_morders_new(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать новые заявки (new/analyzing/reviewed)."""
    await callback.answer()
    await _send_orders_list(
        callback,
        session_factory,
        [OrderStatus.new, OrderStatus.analyzing, OrderStatus.reviewed],
        "Новые заявки",
    )


@router.callback_query(F.data == "morders:assigned")
async def handle_morders_assigned(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать заявки в работе (assigned)."""
    await callback.answer()
    await _send_orders_list(
        callback,
        session_factory,
        [OrderStatus.assigned],
        "Заявки в работе",
    )


@router.callback_query(F.data == "morders:completed")
async def handle_morders_completed(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать завершённые заявки (completed)."""
    await callback.answer()
    await _send_orders_list(
        callback,
        session_factory,
        [OrderStatus.completed],
        "Завершённые заявки",
    )


@router.callback_query(F.data == "morders:all")
async def handle_morders_all(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать все заявки."""
    await callback.answer()
    await _send_orders_list(
        callback,
        session_factory,
        list(OrderStatus),
        "Все заявки",
    )


@router.callback_query(F.data == "morders:search")
async def handle_morders_search(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать поиск заявки по ID."""
    await callback.answer()
    await state.set_state(ManagerPanelStates.searching_order)
    await callback.message.answer(  # type: ignore[union-attr]
        "Введите external_id заявки (например: 87236044):",
        reply_markup=cancel_mgr_kb("mgr:orders"),
    )


@router.message(ManagerPanelStates.searching_order)
async def process_order_search(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Найти заявку по external_id."""
    query_str = message.text.strip() if message.text else ""
    if not query_str:
        await message.answer("Введите ID заявки:")
        return

    async with session_factory() as session:
        result = await session.execute(
            select(Order)
            .options(
                selectinload(Order.assignments).selectinload(OrderAssignment.developer)
            )
            .where(Order.external_id.ilike(f"%{query_str}%"))
            .limit(5)
        )
        orders = list(result.scalars().all())

    await state.clear()

    if not orders:
        await message.answer(
            f"Заявки с ID «{query_str}» не найдены.",
            reply_markup=back_to_manager_kb(),
        )
        return

    items = [_format_order_item(o) for o in orders]
    text = f"<b>Результаты поиска</b> по «{query_str}»:\n\n" + "\n\n".join(items)

    if len(text) > 4096:
        text = text[:4090] + "..."

    await message.answer(
        text,
        reply_markup=back_to_manager_kb(),
    )


# ---------------------------------------------------------------------------
# Разработчики (CP-6.8)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "mgr:devs")
async def handle_mgr_devs(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать список разработчиков."""
    await callback.answer()

    async with session_factory() as session:
        result = await session.execute(
            select(TeamMember)
            .where(TeamMember.role == TeamRole.developer)
            .order_by(TeamMember.name)
        )
        developers = list(result.scalars().all())

    if not developers:
        await callback.message.edit_text(  # type: ignore[union-attr]
            "Разработчики не найдены.",
            reply_markup=back_to_manager_kb(),
        )
        return

    await callback.message.edit_text(  # type: ignore[union-attr]
        "<b>Разработчики</b>\n\nВыберите разработчика:",
        reply_markup=developers_list_kb(developers),
    )


@router.callback_query(F.data.startswith("mdev:") & ~F.data.startswith("mdev:history:") & ~F.data.startswith("mdev:assign:"))
async def handle_mdev_detail(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать детали разработчика: стек, статистика."""
    await callback.answer()
    developer_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        # Загружаем разработчика с назначениями
        result = await session.execute(
            select(TeamMember)
            .options(selectinload(TeamMember.assignments))
            .where(TeamMember.id == developer_id)
        )
        dev = result.scalar_one_or_none()

        if not dev:
            await callback.message.answer("Разработчик не найден.")  # type: ignore[union-attr]
            return

        # Считаем статистику
        active_statuses = [AssignmentStatus.editing, AssignmentStatus.pending]
        completed_statuses = [AssignmentStatus.approved, AssignmentStatus.sent]

        active_count = sum(1 for a in dev.assignments if a.status in active_statuses)
        completed_count = sum(1 for a in dev.assignments if a.status in completed_statuses)

        # Стек
        stack_list = dev.tech_stack or []
        stack_str = ", ".join(stack_list) if stack_list else "—"

        username_str = f" (@{dev.tg_username})" if dev.tg_username else ""
        active_badge = "активен" if dev.is_active else "неактивен"

        text = (
            f"<b>{dev.name}{username_str}</b>\n\n"
            f"Статус: {active_badge}\n"
            f"Стек: {stack_str}\n"
            f"Активных заявок: {active_count}\n"
            f"Завершённых: {completed_count}"
        )

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=developer_detail_kb(developer_id),
    )
    logger.info("Просмотр разработчика developer_id=%s", developer_id)


@router.callback_query(F.data.startswith("mdev:history:"))
async def handle_mdev_history(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать историю заявок разработчика."""
    await callback.answer()
    developer_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    async with session_factory() as session:
        dev_result = await session.execute(
            select(TeamMember).where(TeamMember.id == developer_id)
        )
        dev = dev_result.scalar_one_or_none()

        if not dev:
            await callback.message.answer("Разработчик не найден.")  # type: ignore[union-attr]
            return

        # Загружаем назначения с заказами
        result = await session.execute(
            select(OrderAssignment)
            .options(selectinload(OrderAssignment.order))
            .where(OrderAssignment.developer_id == developer_id)
            .order_by(OrderAssignment.id.desc())
            .limit(10)
        )
        assignments = list(result.scalars().all())

        dev_name = dev.name

    if not assignments:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"<b>История заявок: {dev_name}</b>\n\nЗаявок не найдено.",
            reply_markup=developer_detail_kb(developer_id),
        )
        return

    items: list[str] = []
    for a in assignments:
        order = a.order
        order_id = order.external_id if order else "—"
        order_title = order.title if order else "—"
        status_label = _ASSIGNMENT_STATUS_LABEL.get(a.status.value, a.status.value)
        price_str = f"{a.price_final:,} руб.".replace(",", " ") if a.price_final else "—"
        items.append(
            f"#{order_id} — {order_title}\n"
            f"  Статус: {status_label} | Цена: {price_str}"
        )

    text = f"<b>История заявок: {dev_name}</b>\n\n" + "\n\n".join(items)
    if len(text) > 4096:
        text = text[:4090] + "..."

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=developer_detail_kb(developer_id),
    )


# ---------------------------------------------------------------------------
# Скопировать отклик (copy_response)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("copy_response:"))
async def handle_copy_response(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Отправить текст отклика отдельным сообщением для копирования."""
    response_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(ManagerResponse).where(ManagerResponse.id == response_id)
        )
        resp = result.scalar_one_or_none()

    if not resp:
        await callback.answer("Отклик не найден.", show_alert=True)
        return

    text = resp.edited_text or resp.response_text or "Текст отклика пуст"
    await callback.answer()
    # Отправляем отдельным сообщением без HTML — для удобного копирования
    await callback.message.answer(text, parse_mode=None)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Назначить разработчика на заявку (mdev:assign)
# ---------------------------------------------------------------------------


@router.callback_query(F.data.startswith("mdev:assign:"))
async def handle_mdev_assign(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать список доступных заявок для назначения разработчика."""
    await callback.answer()
    developer_id = int(callback.data.split(":")[2])  # type: ignore[union-attr]

    async with session_factory() as session:
        dev_result = await session.execute(
            select(TeamMember).where(TeamMember.id == developer_id)
        )
        dev = dev_result.scalar_one_or_none()
        if not dev:
            await callback.message.answer("Разработчик не найден.")  # type: ignore[union-attr]
            return

        # Находим новые заявки без назначения
        result = await session.execute(
            select(Order)
            .where(Order.status == OrderStatus.new)
            .order_by(Order.id.desc())
            .limit(10)
        )
        orders = list(result.scalars().all())

    if not orders:
        await callback.message.edit_text(  # type: ignore[union-attr]
            f"<b>Назначение: {dev.name}</b>\n\nНет свободных заявок.",
            reply_markup=developer_detail_kb(developer_id),
        )
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = []
    for o in orders:
        buttons.append([
            InlineKeyboardButton(
                text=f"#{o.external_id} — {(o.title or '')[:40]}",
                callback_data=f"assign:{developer_id}:{o.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data=f"mdev:{developer_id}")])

    await callback.message.edit_text(  # type: ignore[union-attr]
        f"<b>Назначить {dev.name} на заявку:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("assign:"))
async def handle_assign_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Назначить разработчика на конкретную заявку."""
    parts = callback.data.split(":")  # type: ignore[union-attr]
    developer_id = int(parts[1])
    order_id = int(parts[2])

    async with session_factory() as session:
        order_result = await session.execute(
            select(Order)
            .options(selectinload(Order.analyses))
            .where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()
        if not order:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        # Определяем менеджера
        manager_id = None
        if callback.from_user:
            mgr_result = await session.execute(
                select(TeamMember).where(TeamMember.tg_id == callback.from_user.id)
            )
            mgr = mgr_result.scalar_one_or_none()
            if mgr:
                manager_id = mgr.id

        analysis = order.analyses[0] if order.analyses else None

        assignment = OrderAssignment(
            order_id=order_id,
            developer_id=developer_id,
            status=AssignmentStatus.editing,
            price_final=analysis.price_max if analysis else None,
            timeline_final=analysis.timeline_days if analysis else None,
            stack_final=analysis.stack if analysis else None,
            assigned_by=manager_id,
        )
        session.add(assignment)
        order.status = OrderStatus.assigned
        await session.commit()

    await callback.answer("Разработчик назначен!", show_alert=True)
    await callback.message.edit_text(  # type: ignore[union-attr]
        f"Разработчик назначен на заявку #{order.external_id}.",
        reply_markup=developer_detail_kb(developer_id),
    )
    logger.info("Менеджер назначил developer_id=%s на order_id=%s", developer_id, order_id)


# ---------------------------------------------------------------------------
# Аналитика (CP-6.11)
# ---------------------------------------------------------------------------


@router.callback_query(F.data == "mgr:analytics")
async def handle_mgr_analytics(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать аналитику по заявкам."""
    from src.bot.services.analytics import (
        get_all_developers_stats,
        get_manager_stats,
        get_system_stats,
    )

    await callback.answer("Считаем аналитику...")

    async with session_factory() as session:
        sys_stats = await get_system_stats(session)
        devs_7d = await get_all_developers_stats(session, days=7)
        devs_30d = await get_all_developers_stats(session, days=30)
        mgr_7d = await get_manager_stats(session, days=7)
        mgr_30d = await get_manager_stats(session, days=30)

    def _fmt_price(val: int | None) -> str:
        return f"{val:,} руб.".replace(",", " ") if val else "—"

    text = (
        "<b>Аналитика</b>\n\n"
        "<b>Система:</b>\n"
        f"  Заявок всего: {sys_stats['total_orders']}\n"
        f"  Взято: {sys_stats['total_assigned']}\n"
        f"  Утверждено откликов: {sys_stats['total_approved']}\n"
        f"  Отправлено клиентам: {sys_stats['total_sent_to_client']}\n"
        f"  Расходы на отклики: {sys_stats['total_response_cost']} руб.\n"
    )

    # Разработчики за 7 дней
    if devs_7d:
        text += "\n<b>Разработчики (7 дней):</b>\n"
        for d in devs_7d:
            if d["taken"] == 0 and d["approved"] == 0:
                continue
            display = f"@{d['tg_username']}" if d["tg_username"] else d["name"]
            text += (
                f"  {display}: взял {d['taken']}, "
                f"утверждено {d['approved']}, "
                f"ср. чек {_fmt_price(d['avg_price'])}\n"
            )

    # Разработчики за 30 дней
    if devs_30d:
        text += "\n<b>Разработчики (30 дней):</b>\n"
        for d in devs_30d:
            if d["taken"] == 0 and d["approved"] == 0:
                continue
            display = f"@{d['tg_username']}" if d["tg_username"] else d["name"]
            text += (
                f"  {display}: взял {d['taken']}, "
                f"утверждено {d['approved']}, "
                f"ср. чек {_fmt_price(d['avg_price'])}\n"
            )

    # Менеджер
    text += (
        f"\n<b>Менеджер (7 дней):</b>\n"
        f"  Откликов отправлено: {mgr_7d['responses_sent']}\n"
        f"  Расходы: {mgr_7d['total_response_cost']} руб.\n"
        f"\n<b>Менеджер (30 дней):</b>\n"
        f"  Откликов отправлено: {mgr_30d['responses_sent']}\n"
        f"  Расходы: {mgr_30d['total_response_cost']} руб."
    )

    await callback.message.edit_text(  # type: ignore[union-attr]
        text,
        reply_markup=back_to_manager_kb(),
    )
    logger.info("Менеджер запросил аналитику")
