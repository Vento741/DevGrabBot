"""Обработчики заявок в групповом чате."""
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.config import Settings
from src.core.models import (
    AiAnalysis,
    AssignmentStatus,
    Order,
    OrderAssignment,
    OrderStatus,
    TeamMember,
    TeamRole,
)
from src.ai.context import OrderContext
from src.bot.keyboards.orders import order_actions_kb, order_taken_kb
from src.bot.keyboards.review import review_actions_kb

logger = logging.getLogger(__name__)

router = Router(name="orders")


def format_price_range(
    price_min: int | None, price_max: int | None, fallback: str = "не указана",
) -> str:
    """Форматирование диапазона цен."""
    parts: list[str] = []
    if price_min:
        parts.append(f"от {int(price_min):,}".replace(",", " "))
    if price_max:
        parts.append(f"до {int(price_max):,}".replace(",", " "))
    return (" ".join(parts) + " руб.") if parts else fallback


def relevance_bar(score: int) -> str:
    """Визуальный индикатор релевантности."""
    if score >= 80:
        return f"HIGH {score}%"
    elif score >= 50:
        return f"MEDIUM {score}%"
    else:
        return f"LOW {score}%"


def format_order_message(analysis: AiAnalysis, order: Order) -> str:
    """Сформировать HTML-сообщение о заявке для группового чата.

    Args:
        analysis: Результат AI-анализа заявки.
        order: Исходная заявка.

    Returns:
        Отформатированный HTML-текст.
    """
    stack_str = ", ".join(analysis.stack) if analysis.stack else "не определён"
    price_str = format_price_range(analysis.price_min, analysis.price_max)
    timeline_str = analysis.timeline_days or "не указаны"

    return (
        f"<b>Новая заявка</b> | {order.external_id}\n"
        f"<b>{order.title}</b>\n\n"
        f"<b>AI-выжимка:</b>\n{analysis.summary}\n\n"
        f"<b>Стек:</b> {stack_str}\n"
        f"<b>Оценка цены:</b> {price_str}\n"
        f"<b>Сроки:</b> {timeline_str} дн.\n"
        f"<b>Сложность:</b> {analysis.complexity}\n"
        f"<b>Релевантность:</b> {relevance_bar(analysis.relevance_score)}\n"
    )


async def send_order_to_group(
    bot: Bot,
    chat_id: int,
    order_id: int,
    analysis: AiAnalysis,
    order: Order,
) -> None:
    """Отправить заявку в групповой чат с кнопками действий.

    Args:
        bot: Экземпляр Telegram-бота.
        chat_id: ID группового чата.
        order_id: ID заявки в БД.
        analysis: Результат AI-анализа.
        order: Исходная заявка.
    """
    text = format_order_message(analysis, order)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=order_actions_kb(order_id),
    )
    logger.info("Заявка #%s отправлена в групповой чат", order.external_id)


@router.callback_query(F.data.startswith("take:"))
async def handle_take_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Разработчик берёт заявку из группового чата."""
    await callback.answer()
    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    user = callback.from_user

    async with session_factory() as session:
        # Проверяем, что пользователь — зарегистрированный разработчик
        result = await session.execute(
            select(TeamMember).where(
                TeamMember.tg_id == user.id,
                TeamMember.is_active.is_(True),
            )
        )
        member = result.scalar_one_or_none()
        if not member:
            await callback.answer(
                "Вы не зарегистрированы в команде.", show_alert=True,
            )
            return

        # Только разработчики могут брать заявки
        if member.role != TeamRole.developer:
            await callback.answer(
                "Только разработчики могут брать заявки.", show_alert=True,
            )
            return

        # Проверяем, что заявка ещё не взята (+ загружаем анализ одним запросом)
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

        if order.status == OrderStatus.assigned:
            await callback.answer("Заявка уже взята другим разработчиком.", show_alert=True)
            return

        analysis = order.analyses[0] if order.analyses else None

        # Создаём назначение
        assignment = OrderAssignment(
            order_id=order_id,
            developer_id=member.id,
            status=AssignmentStatus.editing,
            taken_at=datetime.utcnow(),
            price_final=analysis.price_max if analysis else None,
            timeline_final=analysis.timeline_days if analysis else None,
            stack_final=analysis.stack if analysis else None,
            group_message_id=callback.message.message_id if callback.message else None,
        )
        session.add(assignment)
        order.status = OrderStatus.assigned
        await session.commit()
        await session.refresh(assignment)

        # DM-уведомление только админу (в группе и так видно кто взял)
        other_devs_result = await session.execute(
            select(TeamMember).where(
                TeamMember.is_active.is_(True),
                TeamMember.tg_id == settings.admin_tg_id,
                TeamMember.tg_id != user.id,
            )
        )
        other_devs = list(other_devs_result.scalars().all())

        logger.info(
            "Заявка #%s взята разработчиком %s (tg_id=%s)",
            order.external_id, member.name, user.id,
        )

    # Обновляем сообщение в группе: добавляем время взятия (МСК), меняем кнопки
    msk = timezone(timedelta(hours=3))
    taken_time = datetime.now(msk).strftime("%H:%M %d.%m.%Y")
    dev_display = f"@{user.username}" if user.username else user.full_name
    await callback.message.edit_text(  # type: ignore[union-attr]
        text=(
            callback.message.text  # type: ignore[union-attr]
            + f"\n\n<b>Взята:</b> {taken_time}"
        ),
        reply_markup=order_taken_kb(order_id, order.external_id, dev_display),
    )

    # Уведомляем других разработчиков о взятии заявки
    taken_time_short = datetime.now(msk).strftime("%H:%M")
    notify_text = (
        f"Заявка <b>{order.external_id}</b> — «{order.title}» "
        f"взята {dev_display} в {taken_time_short}"
    )
    for dev in other_devs:
        try:
            await callback.bot.send_message(  # type: ignore[union-attr]
                chat_id=dev.tg_id,
                text=notify_text,
            )
        except Exception:
            logger.warning("Не удалось уведомить %s (tg_id=%s)", dev.name, dev.tg_id)

    # Формируем сообщение в личку разработчику
    if analysis:
        ctx = OrderContext.from_order_data(order, analysis)
        stack_str = ", ".join(ctx.stack) if ctx.stack else "—"
        price_display = format_price_range(ctx.price_min, ctx.price_max, fallback="—")

        review_text = (
            f"<b>Заявка:</b> {order.external_id} — {order.title}\n\n"
            f"<b>AI-выжимка:</b>\n{ctx.summary}\n\n"
        )

        if ctx.has_client_requirements:
            review_text += f"<b>Пожелания заказчика:</b>\n{ctx.client_requirements}\n\n"

        response_price_str = f"{order.response_price} руб." if order.response_price else "—"
        review_text += (
            f"<b>Стек:</b> {stack_str}\n"
            f"<b>AI-оценка цены:</b> {price_display}\n"
            f"<b>Цена (можно изменить):</b> {analysis.price_max or '—'} руб.\n"
            f"<b>Сроки:</b> {analysis.timeline_days or '—'} дн.\n"
            f"<b>Сложность:</b> {analysis.complexity}\n"
            f"<b>Цена отклика:</b> {response_price_str}\n"
        )

        if ctx.questions:
            q_list = "\n".join(f"  - {q}" for q in ctx.questions[:5])
            review_text += f"\n<b>Вопросы клиенту:</b>\n{q_list}\n"

        if ctx.risks and ctx.risks != "Нет явных рисков":
            review_text += f"\n<b>Риски:</b> {ctx.risks}\n"

        review_text += (
            f"\n<b>Черновик отклика:</b>\n<i>{analysis.response_draft}</i>\n\n"
            "Отредактируйте параметры или утвердите отклик:"
        )
    else:
        review_text = (
            f"<b>Заявка:</b> {order.external_id} — {order.title}\n\n"
            "AI-анализ не найден. Укажите параметры вручную:"
        )

    await callback.bot.send_message(  # type: ignore[union-attr]
        chat_id=user.id,
        text=review_text,
        reply_markup=review_actions_kb(assignment.id, order_id, order.external_id),
    )


@router.callback_query(F.data.startswith("skip:"))
async def handle_skip_order(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Пропуск заявки (отметка как skipped)."""
    await callback.answer("Заявка пропущена.")
    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        order_result = await session.execute(
            select(Order).where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()
        if order and order.status not in (OrderStatus.assigned, OrderStatus.completed):
            order.status = OrderStatus.skipped
            await session.commit()

    await callback.message.edit_text(  # type: ignore[union-attr]
        text=(
            callback.message.text  # type: ignore[union-attr]
            + "\n\n<b>Пропущена</b>"
        ),
        reply_markup=None,
    )


@router.callback_query(F.data.startswith("taken_info:"))
async def handle_taken_info(callback: CallbackQuery) -> None:
    """Информационная кнопка 'Взято' — просто показываем alert."""
    await callback.answer("Заявка уже взята.", show_alert=True)


@router.callback_query(F.data.startswith("materials:"))
async def handle_show_materials(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Отправить материалы (вложения) заявки в личку разработчику."""
    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        order_result = await session.execute(
            select(Order).where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()

    if not order or not order.materials:
        await callback.answer("Материалы не найдены", show_alert=True)
        return

    materials = order.materials
    images = [m for m in materials if m.get("type") == "image" and m.get("url")]
    files = [m for m in materials if m.get("type") == "file" and m.get("url")]
    user_id = callback.from_user.id

    try:
        # Изображения — через media group (батчами по 10)
        if images:
            from aiogram.types import InputMediaPhoto

            for i in range(0, len(images), 10):
                batch = images[i:i + 10]
                media = []
                for idx, img in enumerate(batch):
                    caption = (
                        f"Материалы заявки #{order.external_id} "
                        f"({len(images)} изобр.)"
                    ) if idx == 0 and i == 0 else None
                    media.append(InputMediaPhoto(media=img["url"], caption=caption))
                await callback.bot.send_media_group(  # type: ignore[union-attr]
                    chat_id=user_id, media=media,
                )

        # Документы — по одному
        if files:
            from aiogram.types import URLInputFile

            for f in files:
                await callback.bot.send_document(  # type: ignore[union-attr]
                    chat_id=user_id,
                    document=URLInputFile(f["url"], filename=f.get("name", "file")),
                )

        count = len(images) + len(files)
        await callback.answer(f"Отправлено {count} материал(ов) в личку", show_alert=False)
    except Exception:
        logger.exception("Не удалось отправить материалы заявки #%s", order.external_id)
        await callback.answer(
            "Сначала напишите боту в личку /start", show_alert=True,
        )


@router.callback_query(F.data.startswith("original:"))
async def handle_show_original(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать оригинальный текст заявки."""
    order_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        order_result = await session.execute(
            select(Order).where(Order.id == order_id)
        )
        order = order_result.scalar_one_or_none()

    if not order:
        await callback.answer("Заказ не найден", show_alert=True)
        return

    text = order.raw_text or "Оригинал не сохранён"
    if len(text) > 4000:
        text = text[:4000] + "…"

    try:
        await callback.bot.send_message(  # type: ignore[union-attr]
            chat_id=callback.from_user.id,
            text=f"<b>Оригинал заявки #{order.external_id}:</b>\n\n{text}",
        )
        await callback.answer("Оригинал отправлен в личку", show_alert=False)
    except Exception:
        await callback.answer(
            "Сначала напишите боту в личку /start", show_alert=True
        )
