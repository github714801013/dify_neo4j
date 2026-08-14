"""persist MCP provider transport

Revision ID: d4e5f6a7b8c9
Revises: c8d4e9f1a2b3
Create Date: 2026-08-14 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c8d4e9f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_mcp_providers",
        sa.Column("transport", sa.String(length=32), nullable=False, server_default=sa.text("'sse'")),
    )


def downgrade() -> None:
    op.drop_column("tool_mcp_providers", "transport")
