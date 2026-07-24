"""add graph job index node scope

Revision ID: a4b5c6d7e8f9
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-23 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dataset_graph_index_jobs") as batch_op:
        batch_op.add_column(
            sa.Column("index_node_id", sa.String(length=255), nullable=False, server_default="__dataset__")
        )
        batch_op.drop_constraint("dataset_graph_index_job_unique", type_="unique")
        batch_op.create_unique_constraint(
            "dataset_graph_index_job_unique",
            ["dataset_id", "document_id", "index_node_id", "source_version", "graph_version"],
        )
        batch_op.create_index("dataset_graph_index_job_scope_idx", ["dataset_id", "index_node_id"])


def downgrade() -> None:
    with op.batch_alter_table("dataset_graph_index_jobs") as batch_op:
        batch_op.drop_index("dataset_graph_index_job_scope_idx")
        batch_op.drop_constraint("dataset_graph_index_job_unique", type_="unique")
        batch_op.create_unique_constraint(
            "dataset_graph_index_job_unique",
            ["dataset_id", "document_id", "source_version", "graph_version"],
        )
        batch_op.drop_column("index_node_id")
