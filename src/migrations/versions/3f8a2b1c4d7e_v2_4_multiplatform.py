"""v2.4 multi-platform: SourcePlatform enum, composite unique, url, platforms

Revision ID: 3f8a2b1c4d7e
Revises: 172fb6762889
Create Date: 2026-04-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3f8a2b1c4d7e"
down_revision: Union[str, None] = "172fb6762889"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Создаём PostgreSQL enum sourceplatform.
    sourceplatform = sa.Enum("profiru", "kwork", name="sourceplatform")
    sourceplatform.create(bind, checkfirst=True)

    # 2. orders: конвертируем platform (String → enum), добавляем url.
    op.execute(
        "ALTER TABLE orders "
        "ALTER COLUMN platform DROP DEFAULT, "
        "ALTER COLUMN platform TYPE sourceplatform USING platform::sourceplatform, "
        "ALTER COLUMN platform SET DEFAULT 'profiru'::sourceplatform"
    )

    op.add_column("orders", sa.Column("url", sa.Text(), nullable=True))

    # Backfill url для существующих Profi.ru заявок.
    op.execute(
        "UPDATE orders "
        "SET url = 'https://profi.ru/backoffice/n.php?o=' || external_id "
        "WHERE url IS NULL AND platform = 'profiru'"
    )

    # 3. Меняем unique constraint на orders: external_id → (platform, external_id).
    # Имя автосгенерированного constraint зависит от версии SQLAlchemy;
    # ищем и удаляем его динамически.
    constraint_name = bind.execute(
        sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'orders'::regclass "
            "AND contype = 'u' "
            "AND pg_get_constraintdef(oid) = 'UNIQUE (external_id)'"
        )
    ).scalar()
    if constraint_name:
        op.execute(f'ALTER TABLE orders DROP CONSTRAINT "{constraint_name}"')

    op.create_unique_constraint(
        "uq_orders_platform_external_id",
        "orders",
        ["platform", "external_id"],
    )

    # 4. team_members: добавляем platforms JSON с дефолтом ["profiru", "kwork"].
    op.add_column(
        "team_members",
        sa.Column(
            "platforms",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("""'["profiru", "kwork"]'::json"""),
        ),
    )
    # Убираем server_default (модель держит default на уровне Python).
    op.alter_column("team_members", "platforms", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_column("team_members", "platforms")

    op.drop_constraint("uq_orders_platform_external_id", "orders", type_="unique")
    op.create_unique_constraint(
        "uq_orders_external_id", "orders", ["external_id"]
    )

    op.drop_column("orders", "url")

    op.execute(
        "ALTER TABLE orders "
        "ALTER COLUMN platform DROP DEFAULT, "
        "ALTER COLUMN platform TYPE VARCHAR(50) USING platform::text, "
        "ALTER COLUMN platform SET DEFAULT 'profiru'"
    )

    sourceplatform = sa.Enum("profiru", "kwork", name="sourceplatform")
    sourceplatform.drop(bind, checkfirst=True)
