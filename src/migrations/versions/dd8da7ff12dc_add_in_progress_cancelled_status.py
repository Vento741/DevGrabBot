"""add_in_progress_cancelled_status

Revision ID: dd8da7ff12dc
Revises: 6a78c2818f95
Create Date: 2026-03-11 13:43:34.276555

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd8da7ff12dc'
down_revision: Union[str, None] = '6a78c2818f95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE assignmentstatus ADD VALUE IF NOT EXISTS 'in_progress'")
    op.execute("ALTER TYPE assignmentstatus ADD VALUE IF NOT EXISTS 'cancelled'")
    op.add_column(
        "order_assignments",
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "order_assignments",
        sa.Column("in_progress_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_assignments", "in_progress_at")
    op.drop_column("order_assignments", "cancelled_at")
