"""Handlers для PM-действий в групповом чате (callback prefix pmg:*)."""
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InputMediaPhoto
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.models import (
    AiAnalysis,
    AssignmentStatus,
    Order,
    OrderAssignment,
    OrderStatus,
    TeamMember,
    TeamRole,
)
from src.bot.keyboards.orders import pm_group_taken_kb
from src.bot.utils.platforms import get_order_url, get_platform_label

logger = logging.getLogger(__name__)
router = Router(name="group_pm")


def _is_pm_or_admin(member: TeamMember) -> bool:
    """Проверить, является ли участник менеджером или админом."""
    return member.role == TeamRole.manager or getattr(member, "is_admin", False)


async def _get_member(session: AsyncSession, tg_id: int) -> TeamMember | None:
    """Загрузить TeamMember по telegram_id."""
    result = await session.execute(
        select(TeamMember).where(
            TeamMember.tg_id == tg_id,
            TeamMember.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


def _format_full_analysis(order: Order, analysis: AiAnalysis | None) -> str:
    """Форматирует полный AI-анализ для отправки в личку PM."""
    if not analysis:
        return f"<b>Заявка #{order.external_id}</b>\n\nAI-анализ отсутствует."

    stack_str = ", ".join(analysis.stack) if analysis.stack else "—"
    price_parts = []
    if analysis.price_min:
        price_parts.append(f"от {int(analysis.price_min):,}".replace(",", " "))
    if analysis.price_max:
        price_parts.append(f"до {int(analysis.price_max):,}".replace(",", " "))
    price_str = (" ".join(price_parts) + " руб.") if price_parts else "—"

    platform_label = get_platform_label(getattr(order, "platform", "profiru"))

    lines = [
        f"<b>Заявка #{order.external_id}</b>",
        f"<b>Источник:</b> {platform_label}",
        f"<b>{order.title}</b>",
        "",
        f"<b>Выжимка:</b>\n{analysis.summary}",
        "",
        f"<b>Стек:</b> {stack_str}",
        f"<b>Наша оценка:</b> {price_str}",
        f"<b>Наши сроки:</b> {analysis.timeline_days or '—'} дн.",
        f"<b>Сложность:</b> {analysis.complexity}",
        f"<b>Релевантность:</b> {analysis.relevance_score}/10",
    ]

    if hasattr(analysis, "extra_data") and analysis.extra_data:
        client_req = analysis.extra_data.get("client_requirements", "")
        if client_req:
            lines += ["", f"<b>Пожелания заказчика:</b>\n{client_req}"]
        questions = analysis.extra_data.get("questions_to_client", [])
        if questions:
            q_text = "\n".join(f"  - {q}" for q in questions[:5])
            lines += ["", f"<b>Вопросы клиенту:</b>\n{q_text}"]
        risks = analysis.extra_data.get("risks", "")
        if risks and risks != "Нет явных рисков":
            lines += ["", f"<b>Риски:</b> {risks}"]

    if analysis.response_draft:
        draft = analysis.response_draft[:1500]
        lines += ["", f"<b>Черновик отклика:</b>\n<i>{draft}</i>"]

    return "\n".join(lines)


@router.callback_query(F.data.startswith("pmg:take:"))
async def handle_pm_group_take(
    callback: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """PM берёт заявку себе из группы.

    Создаёт OrderAssignment(status=approved), обновляет Order.status=assigned,
    заменяет клавиатуру в группе, отправляет уведомление в личку PM.
    """
    await callback.answer()
    order_id = int(callback.data.split(":")[2])
    user = callback.from_user

    async with session_factory() as session:
        member = await _get_member(session, user.id)
        if not member:
            await callback.answer("Вы не зарегистрированы в команде.", show_alert=True)
            return

        if not _is_pm_or_admin(member):
            await callback.answer(
                "Недоступно. Откликнитесь через DM.", show_alert=True,
            )
            return

        # Загружаем заявку с назначениями
        order_result = await session.execute(
            select(Order)
            .options(
                selectinload(Order.assignments),
                selectinload(Order.analyses),
            )
            .where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()
        if not order:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        # Проверяем нет ли уже активного assignment
        active_assignment = next(
            (
                a for a in order.assignments
                if a.status in (
                    AssignmentStatus.approved,
                    AssignmentStatus.in_progress,
                    AssignmentStatus.editing,
                    AssignmentStatus.pending,
                )
            ),
            None,
        )
        if active_assignment:
            await callback.answer("Заявка уже взята в работу.", show_alert=True)
            return

        analysis = order.analyses[0] if order.analyses else None

        # Создаём назначение
        assignment = OrderAssignment(
            order_id=order_id,
            developer_id=member.id,
            assigned_by=member.id,
            status=AssignmentStatus.approved,
            taken_at=datetime.utcnow(),
            approved_at=datetime.utcnow(),
            price_final=analysis.price_max if analysis else None,
            timeline_final=analysis.timeline_days if analysis else None,
            stack_final=analysis.stack if analysis else None,
        )
        session.add(assignment)
        order.status = OrderStatus.assigned
        await session.commit()
        await session.refresh(assignment)

        logger.info(
            "PM %s (tg_id=%s) взял заявку #%s из группы",
            member.name, user.id, order.external_id,
        )

    # Обновляем клавиатуру в группе
    pm_display = f"@{member.tg_username}" if member.tg_username else member.name
    platform = getattr(order, "platform", "profiru")
    platform_url = get_order_url(platform, order.external_id)
    new_kb = pm_group_taken_kb(order_id, pm_display, platform_url=platform_url)

    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception:
        logger.warning("Не удалось обновить клавиатуру в группе для заявки #%s", order.external_id)

    # DM PM-у
    try:
        await bot.send_message(
            chat_id=user.id,
            text=(
                f"Заявка <b>#{order.external_id}</b> взята в работу.\n"
                f"<b>{order.title}</b>\n\n"
                "Статус: в работе. Можете отвечать клиенту."
            ),
        )
    except Exception:
        logger.warning("Не удалось отправить DM PM-у о взятии заявки #%s", order.external_id)


@router.callback_query(F.data.startswith("pmg:analysis:"))
async def handle_pm_group_analysis(
    callback: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать полный AI-анализ в личку PM — чтобы не засорять группу."""
    await callback.answer("Анализ отправлен вам в личку.")
    order_id = int(callback.data.split(":")[2])
    user = callback.from_user

    async with session_factory() as session:
        member = await _get_member(session, user.id)
        if not member:
            return

        if not _is_pm_or_admin(member):
            await callback.answer(
                "Недоступно. Откликнитесь через DM.", show_alert=True,
            )
            return

        order_result = await session.execute(
            select(Order)
            .options(selectinload(Order.analyses))
            .where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()
        if not order:
            await callback.answer("Заявка не найдена.", show_alert=True)
            return

        analysis = order.analyses[0] if order.analyses else None
        text = _format_full_analysis(order, analysis)

    # Разбиваем на части если длиннее 4096 символов
    try:
        if len(text) <= 4096:
            await bot.send_message(chat_id=user.id, text=text)
        else:
            for i in range(0, len(text), 4096):
                await bot.send_message(chat_id=user.id, text=text[i:i + 4096])
    except Exception:
        logger.warning("Не удалось отправить анализ в DM PM-у для заявки #%s", order_id)
        await callback.answer(
            "Сначала напишите боту в личку /start", show_alert=True,
        )


@router.callback_query(F.data.startswith("pmg:materials:"))
async def handle_pm_group_materials(
    callback: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Отправить материалы заявки в личку PM."""
    order_id = int(callback.data.split(":")[2])
    user = callback.from_user

    async with session_factory() as session:
        member = await _get_member(session, user.id)
        if not member:
            return

        if not _is_pm_or_admin(member):
            await callback.answer(
                "Недоступно. Откликнитесь через DM.", show_alert=True,
            )
            return

        order_result = await session.execute(
            select(Order).where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()

    if not order or not order.materials:
        await callback.answer("Материалы не найдены.", show_alert=True)
        return

    materials = order.materials
    images = [m for m in materials if m.get("type") == "image" and m.get("url")]
    files = [m for m in materials if m.get("type") == "file" and m.get("url")]

    try:
        if images:
            for i in range(0, len(images), 10):
                batch = images[i:i + 10]
                media = []
                for idx, img in enumerate(batch):
                    caption = (
                        f"Материалы заявки #{order.external_id} ({len(images)} изобр.)"
                    ) if idx == 0 and i == 0 else None
                    media.append(InputMediaPhoto(media=img["url"], caption=caption))
                await bot.send_media_group(chat_id=user.id, media=media)

        if files:
            from aiogram.types import URLInputFile
            for f in files:
                await bot.send_document(
                    chat_id=user.id,
                    document=URLInputFile(f["url"], filename=f.get("name", "file")),
                )

        count = len(images) + len(files)
        await callback.answer(f"Отправлено {count} материал(ов) в личку.", show_alert=False)
    except Exception:
        logger.exception("Не удалось отправить материалы заявки #%s PM-у", order.external_id)
        await callback.answer(
            "Сначала напишите боту в личку /start", show_alert=True,
        )


@router.callback_query(F.data.startswith("pmg:hide:"))
async def handle_pm_group_hide(
    callback: CallbackQuery,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Удалить карточку заявки из группового чата. Только PM/admin."""
    order_id = int(callback.data.split(":")[2])
    user = callback.from_user

    async with session_factory() as session:
        member = await _get_member(session, user.id)
        if not member:
            return

        if not _is_pm_or_admin(member):
            await callback.answer(
                "Недоступно. Откликнитесь через DM.", show_alert=True,
            )
            return

        # Сбрасываем group_message_id в Order
        order_result = await session.execute(
            select(Order).where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()
        if order:
            order.group_message_id = None
            await session.commit()

    try:
        await bot.delete_message(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
        )
        logger.info(
            "PM %s (tg_id=%s) скрыл заявку order_id=%s из группы",
            member.name, user.id, order_id,
        )
    except Exception:
        logger.warning("Не удалось удалить сообщение заявки order_id=%s из группы", order_id)
        await callback.answer("Не удалось удалить сообщение.", show_alert=True)


@router.callback_query(F.data.startswith("pmg:info:"))
async def handle_pm_group_info(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Информационная кнопка 'кто взял' — только всплывающий alert."""
    order_id = int(callback.data.split(":")[2])

    async with session_factory() as session:
        order_result = await session.execute(
            select(Order)
            .options(
                selectinload(Order.assignments).selectinload(OrderAssignment.developer)
            )
            .where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()

    if not order:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    active = next(
        (
            a for a in order.assignments
            if a.status in (
                AssignmentStatus.approved,
                AssignmentStatus.in_progress,
            )
        ),
        None,
    )
    if active and active.developer:
        dev = active.developer
        who = f"@{dev.tg_username}" if dev.tg_username else dev.name
        taken_str = (
            active.taken_at.strftime("%d.%m.%Y %H:%M UTC")
            if active.taken_at else "—"
        )
        await callback.answer(
            f"В работе: {who}\nВзята: {taken_str}",
            show_alert=True,
        )
    else:
        await callback.answer("Информация о назначении не найдена.", show_alert=True)
