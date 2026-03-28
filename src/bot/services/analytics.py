"""Сервис аналитики — SQL-запросы для статистики по заявкам, разработчикам, откликам."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import (
    AssignmentStatus,
    ManagerResponse,
    Order,
    OrderAssignment,
    TeamMember,
    TeamRole,
)

logger = logging.getLogger(__name__)

# Статусы, считающиеся «утверждёнными»
_COMPLETED_STATUSES = (AssignmentStatus.approved, AssignmentStatus.sent)


def _cutoff(days: int | None) -> datetime | None:
    """Вернуть datetime-порог для фильтрации по последним N дням."""
    if days is None:
        return None
    return datetime.utcnow() - timedelta(days=days)


async def get_system_stats(session: AsyncSession) -> dict:
    """Общесистемная статистика.

    Returns:
        total_orders, total_assigned, total_approved,
        total_sent_to_client, total_response_cost
    """
    total_orders = (
        await session.execute(select(func.count(Order.id)))
    ).scalar_one()

    total_assigned = (
        await session.execute(select(func.count(OrderAssignment.id)))
    ).scalar_one()

    total_approved = (
        await session.execute(
            select(func.count(OrderAssignment.id)).where(
                OrderAssignment.status.in_(_COMPLETED_STATUSES)
            )
        )
    ).scalar_one()

    total_sent_to_client = (
        await session.execute(
            select(func.count(ManagerResponse.id)).where(
                ManagerResponse.sent_to_client.is_(True)
            )
        )
    ).scalar_one()

    # Сумма стоимости откликов: ManagerResponse → OrderAssignment → Order
    total_response_cost = (
        await session.execute(
            select(func.coalesce(func.sum(Order.response_price), 0))
            .select_from(ManagerResponse)
            .join(OrderAssignment, OrderAssignment.id == ManagerResponse.assignment_id)
            .join(Order, Order.id == OrderAssignment.order_id)
            .where(ManagerResponse.sent_to_client.is_(True))
        )
    ).scalar_one()

    return {
        "total_orders": total_orders,
        "total_assigned": total_assigned,
        "total_approved": total_approved,
        "total_sent_to_client": total_sent_to_client,
        "total_response_cost": total_response_cost or 0,
    }


async def get_developer_stats(
    session: AsyncSession,
    developer_id: int,
    days: int | None = None,
) -> dict:
    """Статистика по конкретному разработчику.

    Args:
        developer_id: TeamMember.id
        days: ограничить N последними днями (None = за всё время)

    Returns:
        taken, approved, avg_price, avg_time_to_take_hours
    """
    cutoff = _cutoff(days)

    base = select(func.count(OrderAssignment.id)).where(
        OrderAssignment.developer_id == developer_id
    )
    if cutoff:
        base = base.where(OrderAssignment.taken_at >= cutoff)

    taken = (await session.execute(base)).scalar_one()

    approved_q = (
        select(func.count(OrderAssignment.id)).where(
            OrderAssignment.developer_id == developer_id,
            OrderAssignment.status.in_(_COMPLETED_STATUSES),
        )
    )
    if cutoff:
        approved_q = approved_q.where(OrderAssignment.taken_at >= cutoff)
    approved = (await session.execute(approved_q)).scalar_one()

    avg_q = (
        select(func.avg(OrderAssignment.price_final)).where(
            OrderAssignment.developer_id == developer_id,
            OrderAssignment.price_final.isnot(None),
            OrderAssignment.status.in_(_COMPLETED_STATUSES),
        )
    )
    if cutoff:
        avg_q = avg_q.where(OrderAssignment.taken_at >= cutoff)
    avg_price_raw = (await session.execute(avg_q)).scalar_one()
    avg_price = round(avg_price_raw) if avg_price_raw else None

    # Среднее время от публикации заявки до взятия
    time_q = (
        select(
            func.avg(
                func.extract("epoch", OrderAssignment.taken_at)
                - func.extract("epoch", Order.created_at)
            )
        )
        .select_from(OrderAssignment)
        .join(Order, Order.id == OrderAssignment.order_id)
        .where(
            OrderAssignment.developer_id == developer_id,
            OrderAssignment.taken_at.isnot(None),
        )
    )
    if cutoff:
        time_q = time_q.where(OrderAssignment.taken_at >= cutoff)
    avg_seconds = (await session.execute(time_q)).scalar_one()
    avg_time_hours = round(avg_seconds / 3600, 1) if avg_seconds else None

    return {
        "taken": taken,
        "approved": approved,
        "avg_price": avg_price,
        "avg_time_to_take_hours": avg_time_hours,
    }


async def get_all_developers_stats(
    session: AsyncSession,
    days: int | None = None,
) -> list[dict]:
    """Статистика по всем активным разработчикам.

    Returns:
        Список словарей с ключами: name, tg_username, taken, approved, avg_price.
        Отсортирован по taken desc.
    """
    devs_result = await session.execute(
        select(TeamMember).where(
            TeamMember.is_active.is_(True),
            TeamMember.role == TeamRole.developer,
        )
    )
    devs = list(devs_result.scalars().all())

    result = []
    for dev in devs:
        stats = await get_developer_stats(session, dev.id, days)
        result.append({
            "name": dev.name,
            "tg_username": dev.tg_username,
            **stats,
        })

    result.sort(key=lambda x: x["taken"], reverse=True)
    return result


async def get_manager_stats(
    session: AsyncSession,
    days: int | None = None,
) -> dict:
    """Статистика менеджера по откликам.

    Returns:
        responses_total, responses_sent, total_response_cost
    """
    cutoff = _cutoff(days)

    base_total = select(func.count(ManagerResponse.id))
    base_sent = select(func.count(ManagerResponse.id)).where(
        ManagerResponse.sent_to_client.is_(True)
    )
    cost_q = (
        select(func.coalesce(func.sum(Order.response_price), 0))
        .select_from(ManagerResponse)
        .join(OrderAssignment, OrderAssignment.id == ManagerResponse.assignment_id)
        .join(Order, Order.id == OrderAssignment.order_id)
        .where(ManagerResponse.sent_to_client.is_(True))
    )

    if cutoff:
        base_total = base_total.where(ManagerResponse.sent_at >= cutoff)
        base_sent = base_sent.where(ManagerResponse.sent_to_client_at >= cutoff)
        cost_q = cost_q.where(ManagerResponse.sent_to_client_at >= cutoff)

    responses_total = (await session.execute(base_total)).scalar_one()
    responses_sent = (await session.execute(base_sent)).scalar_one()
    total_response_cost = (await session.execute(cost_q)).scalar_one()

    return {
        "responses_total": responses_total,
        "responses_sent": responses_sent,
        "total_response_cost": total_response_cost or 0,
    }


async def get_daily_broadcast_text(session: AsyncSession) -> str | None:
    """Сформировать HTML-текст ежедневной статистики для группы.

    Returns:
        HTML-строку или None если за день активности не было.
    """
    today = datetime.utcnow().strftime("%d.%m.%Y")

    devs = await get_all_developers_stats(session, days=1)
    mgr = await get_manager_stats(session, days=1)

    # Если нет никакой активности — не отправляем
    total_taken = sum(d["taken"] for d in devs)
    if total_taken == 0 and mgr["responses_sent"] == 0:
        return None

    lines = [f"<b>Статистика за {today}</b>", ""]

    if total_taken > 0:
        lines.append("<b>Разработчики:</b>")
        for d in devs:
            if d["taken"] == 0 and d["approved"] == 0:
                continue
            display = f"@{d['tg_username']}" if d["tg_username"] else d["name"]
            lines.append(
                f"  {display}: взял {d['taken']}, утверждено {d['approved']}"
            )
        lines.append("")

    lines.append("<b>Отклики:</b>")
    lines.append(f"  Отправлено клиентам: {mgr['responses_sent']}")
    lines.append(f"  Расходы на отклики: {mgr['total_response_cost']} руб.")

    return "\n".join(lines)
