"""Обработчики уведомлений менеджеру."""
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.core.config import Settings
from src.core.models import (
    AssignmentStatus,
    ManagerResponse,
    OrderAssignment,
    TeamMember,
    TeamRole,
)
from src.bot.keyboards.review import pm_response_kb, pm_status_badge_kb

logger = logging.getLogger(__name__)

router = Router(name="manager")


async def send_to_manager(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    assignment_id: int,
    response_text: str,
) -> None:
    """Отправить готовый отклик менеджеру.

    Находит менеджера в БД и отправляет ему сообщение с деталями
    заявки, исполнителем и текстом отклика.

    Args:
        bot: Экземпляр Telegram-бота.
        settings: Настройки приложения.
        session_factory: Фабрика async-сессий SQLAlchemy.
        assignment_id: ID назначения.
        response_text: Текст сгенерированного отклика.
    """
    async with session_factory() as session:
        # Загружаем назначение с заказом и разработчиком
        result = await session.execute(
            select(OrderAssignment)
            .options(
                selectinload(OrderAssignment.order),
                selectinload(OrderAssignment.developer),
                selectinload(OrderAssignment.manager_response),
            )
            .where(OrderAssignment.id == assignment_id)
        )
        assignment = result.scalar_one_or_none()
        if not assignment:
            logger.error("Назначение %s не найдено для отправки менеджеру", assignment_id)
            return

        order = assignment.order
        developer = assignment.developer

        # Находим менеджера
        mgr_result = await session.execute(
            select(TeamMember).where(
                TeamMember.role == TeamRole.manager,
                TeamMember.is_active.is_(True),
            )
        )
        manager = mgr_result.scalar_one_or_none()
        if not manager:
            logger.error("Активный менеджер не найден — отклик не отправлен")
            return
        chat_id = manager.tg_id

        # Формируем сообщение
        stack_str = ", ".join(assignment.stack_final) if assignment.stack_final else "—"
        response_id = assignment.manager_response.id if assignment.manager_response else 0
        order_external_id = order.external_id
        resp_price_str = f"{order.response_price} руб." if order.response_price else "—"

        text = (
            f"<b>Готовый отклик</b> | {order.external_id}\n\n"
            f"<b>Заявка:</b> {order.title}\n"
            f"<b>Исполнитель:</b> {developer.name}"
            f"{' (@' + developer.tg_username + ')' if developer.tg_username else ''}\n"
            f"<b>Цена:</b> {assignment.price_final or '—'} руб.\n"
            f"<b>Сроки:</b> {assignment.timeline_final or '—'}\n"
            f"<b>Стек:</b> {stack_str}\n"
            f"<b>Цена отклика:</b> {resp_price_str}\n"
        )

        # Заметка от разработчика
        if assignment.custom_notes:
            text += f"\n<b>Заметка от разработчика:</b>\n{assignment.custom_notes}\n"

        text += f"\n<b>Текст отклика:</b>\n{response_text}"

    await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=pm_response_kb(
            assignment_id, response_id, order_external_id,
        ) if response_id else None,
    )
    logger.info(
        "Отклик по заявке #%s отправлен менеджеру (chat_id=%s)",
        order.external_id, chat_id,
    )


@router.callback_query(F.data.startswith("copy_response:"))
async def handle_copy_response(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Показать текст отклика отдельным сообщением для удобного копирования."""
    await callback.answer()
    response_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        result = await session.execute(
            select(ManagerResponse).where(ManagerResponse.id == response_id)
        )
        response = result.scalar_one_or_none()

    if not response:
        await callback.message.answer("Отклик не найден.")  # type: ignore[union-attr]
        return

    # Отправляем чистый текст без форматирования для удобного копирования
    await callback.message.answer(  # type: ignore[union-attr]
        f"<code>{response.response_text}</code>\n\n"
        "<i>Скопируйте текст выше и отправьте заказчику.</i>",
    )


# ---------------------------------------------------------------------------
# PM action handlers — управление жизненным циклом отклика
# ---------------------------------------------------------------------------


async def _notify_developer(bot: Bot, tg_id: int, text: str) -> None:
    """Отправить уведомление разработчику в ЛС."""
    try:
        await bot.send_message(chat_id=tg_id, text=text)
    except Exception:
        logger.warning("Не удалось отправить уведомление dev tg_id=%s", tg_id)


async def _load_assignment_for_pm(
    session: AsyncSession, assignment_id: int,
) -> OrderAssignment | None:
    """Загрузить назначение с заказом, разработчиком и откликом."""
    result = await session.execute(
        select(OrderAssignment)
        .options(
            selectinload(OrderAssignment.order),
            selectinload(OrderAssignment.developer),
            selectinload(OrderAssignment.manager_response),
        )
        .where(OrderAssignment.id == assignment_id)
    )
    return result.scalar_one_or_none()


@router.callback_query(F.data.startswith("pm_sent:"))
async def handle_pm_sent(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """PM отметил отклик как отправленный клиенту."""
    await callback.answer("Отмечено как отправленный")
    assignment_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        assignment = await _load_assignment_for_pm(session, assignment_id)
        if not assignment:
            await callback.message.answer("Назначение не найдено.")  # type: ignore[union-attr]
            return

        assignment.status = AssignmentStatus.sent
        if assignment.manager_response:
            assignment.manager_response.sent_to_client = True
            assignment.manager_response.sent_to_client_at = datetime.utcnow()
            response_id = assignment.manager_response.id
        else:
            response_id = 0

        external_id = assignment.order.external_id
        dev_tg_id = assignment.developer.tg_id
        await session.commit()

    # Уведомляем разработчика
    await _notify_developer(
        bot, dev_tg_id,
        f"Отклик по заявке <b>{external_id}</b> отправлен клиенту \u2705",
    )

    # Обновляем клавиатуру: скрыть "Отправлен", оставить "В работе"/"Отмена"
    try:
        await callback.message.edit_reply_markup(  # type: ignore[union-attr]
            reply_markup=pm_response_kb(
                assignment_id, response_id, external_id,
                hide_sent=True,
            ),
        )
    except Exception:
        pass

    logger.info("PM отметил отклик по заявке %s как отправленный", external_id)


@router.callback_query(F.data.startswith("pm_in_progress:"))
async def handle_pm_in_progress(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """PM отметил заявку как 'в работе'."""
    await callback.answer("В работе")
    assignment_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        assignment = await _load_assignment_for_pm(session, assignment_id)
        if not assignment:
            await callback.message.answer("Назначение не найдено.")  # type: ignore[union-attr]
            return

        assignment.status = AssignmentStatus.in_progress
        assignment.in_progress_at = datetime.utcnow()
        # Автоматически помечаем как отправленный если ещё не отмечен
        if assignment.manager_response and not assignment.manager_response.sent_to_client:
            assignment.manager_response.sent_to_client = True
            assignment.manager_response.sent_to_client_at = datetime.utcnow()

        response_id = assignment.manager_response.id if assignment.manager_response else 0
        external_id = assignment.order.external_id
        dev_tg_id = assignment.developer.tg_id
        await session.commit()

    await _notify_developer(
        bot, dev_tg_id,
        f"Заявка <b>{external_id}</b> — клиент согласовал, приступаем к работе! \U0001f528",
    )

    # Скрыть "Отправлен" + "В работе", оставить только "Отмена"
    try:
        await callback.message.edit_reply_markup(  # type: ignore[union-attr]
            reply_markup=pm_response_kb(
                assignment_id, response_id, external_id,
                hide_sent=True, hide_in_progress=True,
            ),
        )
    except Exception:
        pass

    logger.info("PM перевёл заявку %s в работу", external_id)


@router.callback_query(F.data.startswith("pm_cancel:"))
async def handle_pm_cancel(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    """PM отменил/архивировал заявку."""
    await callback.answer("Заявка отменена")
    assignment_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with session_factory() as session:
        assignment = await _load_assignment_for_pm(session, assignment_id)
        if not assignment:
            await callback.message.answer("Назначение не найдено.")  # type: ignore[union-attr]
            return

        assignment.status = AssignmentStatus.cancelled
        assignment.cancelled_at = datetime.utcnow()

        response_id = assignment.manager_response.id if assignment.manager_response else 0
        external_id = assignment.order.external_id
        dev_tg_id = assignment.developer.tg_id
        await session.commit()

    await _notify_developer(
        bot, dev_tg_id,
        f"Заявка <b>{external_id}</b> отменена менеджером \u274c",
    )

    # Заменяем все кнопки на бейдж
    try:
        await callback.message.edit_reply_markup(  # type: ignore[union-attr]
            reply_markup=pm_status_badge_kb(
                "\u274c Отменена (архив)", response_id, external_id,
            ),
        )
    except Exception:
        pass

    logger.info("PM отменил заявку %s", external_id)
