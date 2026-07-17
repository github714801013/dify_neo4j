"""合并上游 1.16.0-rc1 迁移链与 GraphRAG 迁移链

Revision ID: 1a2b3c4d5e6f
Revises: c3d4e5f6a7b8, f0e1d2c3b4a5
Create Date: 2026-07-17 11:00:00.000000

上游 1.16.0-rc1 在 d9e8f7a6b5c4 之后新增了 a6f1c9d2e8b4 链，
本仓库的 GraphRAG 迁移链 e7f4a9b2c6d1 也从 d9e8f7a6b5c4 分叉，
形成两个 head。本迁移为空 merge，仅合并两条链为单一 head，
便于 flask db upgrade 正常推进。
"""

# Alembic 版本标识。
revision = "1a2b3c4d5e6f"
down_revision = ("c3d4e5f6a7b8", "f0e1d2c3b4a5")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 空 merge：仅合并两条迁移链的 head，不修改 schema。
    pass


def downgrade() -> None:
    # 空 merge：回滚不产生 schema 变化。
    pass
