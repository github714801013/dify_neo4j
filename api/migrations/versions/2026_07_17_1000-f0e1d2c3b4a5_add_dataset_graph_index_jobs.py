"""新增 Dataset GraphRAG 索引 Job 表

Revision ID: f0e1d2c3b4a5
Revises: e7f4a9b2c6d1
Create Date: 2026-07-17 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

import models

# Alembic 版本标识。
revision = "f0e1d2c3b4a5"
down_revision = "e7f4a9b2c6d1"
branch_labels = None
depends_on = None


def _uuid_column(name: str, *, nullable: bool = False, primary_key: bool = False) -> sa.Column:
    kwargs = {"nullable": nullable, "primary_key": primary_key}
    if primary_key and op.get_bind().dialect.name == "postgresql":
        kwargs["server_default"] = sa.text("uuidv7()")
    return sa.Column(name, models.types.StringUUID(), **kwargs)


def upgrade():
    op.create_table(
        "dataset_graph_index_jobs",
        _uuid_column("id", primary_key=True),
        sa.Column("tenant_id", models.types.StringUUID(), nullable=False),
        sa.Column("dataset_id", models.types.StringUUID(), nullable=False),
        sa.Column("document_id", models.types.StringUUID(), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("graph_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "available_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.PrimaryKeyConstraint("id", name=op.f("dataset_graph_index_job_pkey")),
        sa.UniqueConstraint(
            "dataset_id",
            "document_id",
            "source_version",
            "graph_version",
            name=op.f("dataset_graph_index_job_unique"),
        ),
    )
    op.create_index(
        op.f("dataset_graph_index_job_status_available_idx"),
        "dataset_graph_index_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        op.f("dataset_graph_index_job_dataset_document_idx"),
        "dataset_graph_index_jobs",
        ["dataset_id", "document_id"],
    )
    op.create_index(
        op.f("dataset_graph_index_job_tenant_idx"),
        "dataset_graph_index_jobs",
        ["tenant_id"],
    )


def downgrade():
    op.drop_index(op.f("dataset_graph_index_job_tenant_idx"), table_name="dataset_graph_index_jobs")
    op.drop_index(
        op.f("dataset_graph_index_job_dataset_document_idx"), table_name="dataset_graph_index_jobs"
    )
    op.drop_index(
        op.f("dataset_graph_index_job_status_available_idx"), table_name="dataset_graph_index_jobs"
    )
    op.drop_table("dataset_graph_index_jobs")
