"""add_order_notifications_table

Revision ID: a3b5e7f91c02
Revises: dd8da7ff12dc
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a3b5e7f91c02'
down_revision: Union[str, None] = 'dd8da7ff12dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("developer_id", sa.Integer(), sa.ForeignKey("team_members.id"), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_order_notifications_order_id", "order_notifications", ["order_id"])
    op.create_index("ix_order_notifications_developer_id", "order_notifications", ["developer_id"])


def downgrade() -> None:
    op.drop_index("ix_order_notifications_developer_id", "order_notifications")
    op.drop_index("ix_order_notifications_order_id", "order_notifications")
    op.drop_table("order_notifications")
