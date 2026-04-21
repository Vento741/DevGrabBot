"""v2_5_1_unique_constraints

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-04-21 00:00:00.000000

Добавляет два защитных ограничения:
1. UNIQUE на orders.group_message_id — защита от orphan-дублей сообщений в группе.
2. Partial UNIQUE INDEX на order_assignments(order_id) WHERE status IN ('approved', 'in_progress') —
   гарантирует не более одного активного assignment на заявку.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # --- Проверка 1: дубликаты group_message_id в orders ---
    result = bind.execute(sa.text("""
        SELECT group_message_id, COUNT(*)
        FROM orders
        WHERE group_message_id IS NOT NULL
        GROUP BY group_message_id
        HAVING COUNT(*) > 1
    """))
    dups = result.fetchall()
    if dups:
        raise RuntimeError(
            f"Duplicate group_message_id found in orders: {dups}. "
            "Clean up before running this migration."
        )

    # --- Проверка 2: несколько активных assignments на один order ---
    result = bind.execute(sa.text("""
        SELECT order_id, COUNT(*)
        FROM order_assignments
        WHERE status IN ('approved', 'in_progress')
        GROUP BY order_id
        HAVING COUNT(*) > 1
    """))
    dups2 = result.fetchall()
    if dups2:
        raise RuntimeError(
            f"Multiple active assignments (approved/in_progress) for orders: {dups2}. "
            "Resolve conflicts before running this migration."
        )

    # --- Применяем ограничения ---
    op.create_unique_constraint(
        "uq_orders_group_message_id",
        "orders",
        ["group_message_id"],
    )

    op.execute("""
        CREATE UNIQUE INDEX uq_order_assignments_active
        ON order_assignments (order_id)
        WHERE status IN ('approved', 'in_progress')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_order_assignments_active")
    op.drop_constraint("uq_orders_group_message_id", "orders", type_="unique")
