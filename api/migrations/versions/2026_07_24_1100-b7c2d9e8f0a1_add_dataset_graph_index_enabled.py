"""add dataset graph extraction enabled flag

Revision ID: b7c2d9e8f0a1
Revises: a4b5c6d7e8f9
Create Date: 2026-07-24 11:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b7c2d9e8f0a1"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep existing rows NULL so runtime can apply the historical mixed-config fallback.
    with op.batch_alter_table("dataset_graph_configs") as batch_op:
        batch_op.add_column(sa.Column("index_enabled", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dataset_graph_configs") as batch_op:
        batch_op.drop_column("index_enabled")
