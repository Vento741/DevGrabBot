"""v2.5 group_and_filters: add group_message_id to orders

Revision ID: a1b2c3d4e5f6
Revises: 3f8a2b1c4d7e
Create Date: 2026-04-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "3f8a2b1c4d7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("group_message_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "group_message_id")
