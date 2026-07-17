"""新增 Dataset GraphRAG 配置表

Revision ID: e7f4a9b2c6d1
Revises: d9e8f7a6b5c4
Create Date: 2026-07-16 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

import models

# Alembic 版本标识。
revision = "e7f4a9b2c6d1"
down_revision = "d9e8f7a6b5c4"
branch_labels = None
depends_on = None


def _uuid_column(name: str, *, nullable: bool = False, primary_key: bool = False) -> sa.Column:
    kwargs = {"nullable": nullable, "primary_key": primary_key}
    if primary_key and op.get_bind().dialect.name == "postgresql":
        kwargs["server_default"] = sa.text("uuidv7()")
    return sa.Column(name, models.types.StringUUID(), **kwargs)


def upgrade():
    op.create_table(
        "dataset_graph_configs",
        _uuid_column("id", primary_key=True),
        sa.Column("tenant_id", models.types.StringUUID(), nullable=False),
        sa.Column("dataset_id", models.types.StringUUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("query_mode", sa.String(length=32), nullable=False, server_default=sa.text("'hybrid'")),
        sa.Column("graph_top_k", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("graph_max_depth", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("graph_timeout_ms", sa.Integer(), nullable=False, server_default=sa.text("1500")),
        sa.Column("graph_weight", sa.Numeric(6, 4), nullable=False, server_default=sa.text("0.3000")),
        sa.Column("schema_json", sa.JSON(), nullable=False),
        sa.Column("extract_model_config", sa.JSON(), nullable=True),
        sa.Column("graph_version", sa.String(length=64), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.PrimaryKeyConstraint("id", name=op.f("dataset_graph_config_pkey")),
        sa.UniqueConstraint("tenant_id", "dataset_id", name=op.f("dataset_graph_config_tenant_dataset_unique")),
    )
    op.create_index("dataset_graph_config_dataset_id_idx", "dataset_graph_configs", ["dataset_id"])


def downgrade():
    op.drop_index("dataset_graph_config_dataset_id_idx", table_name="dataset_graph_configs")
    op.drop_table("dataset_graph_configs")
