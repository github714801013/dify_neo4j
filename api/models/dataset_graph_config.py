"""持久化的 Dataset 级 GraphRAG 配置。

该表刻意与 Dify 上游 Dataset 模型分离。后续 GraphRAG Hook 将读取它；本阶段
不改变任何既有索引和检索行为。
"""

import sqlalchemy as sa
from pydantic import JsonValue
from sqlalchemy.orm import Mapped, mapped_column

from core.rag.graph.entities import DEFAULT_GRAPH_SCHEMA, GraphQueryMode

from .base import DefaultFieldsDCMixin, TypeBase
from .types import EnumText, StringUUID


class DatasetGraphConfig(DefaultFieldsDCMixin, TypeBase):
    """按 tenant 和 Dataset 标识隔离的 GraphRAG 设置。"""

    __tablename__ = "dataset_graph_configs"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="dataset_graph_config_pkey"),
        sa.UniqueConstraint("tenant_id", "dataset_id", name="dataset_graph_config_tenant_dataset_unique"),
        sa.Index("dataset_graph_config_dataset_id_idx", "dataset_id"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    dataset_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False, server_default=sa.text("false"))
    # NULL only appears on rows created before extraction and retrieval were decoupled.
    index_enabled: Mapped[bool | None] = mapped_column(sa.Boolean, nullable=True, default=None)
    query_mode: Mapped[GraphQueryMode] = mapped_column(
        EnumText(GraphQueryMode, length=32),
        nullable=False,
        default=GraphQueryMode.HYBRID,
        server_default=sa.text("'hybrid'"),
    )
    graph_top_k: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=10, server_default=sa.text("10"))
    graph_max_depth: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1, server_default=sa.text("1"))
    graph_timeout_ms: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1500, server_default=sa.text("1500")
    )
    graph_weight: Mapped[float] = mapped_column(
        sa.Numeric(6, 4, asdecimal=False), nullable=False, default=0.3, server_default=sa.text("0.3000")
    )
    schema_json: Mapped[dict[str, JsonValue]] = mapped_column(
        sa.JSON,
        nullable=False,
        default_factory=lambda: DEFAULT_GRAPH_SCHEMA.model_dump(mode="json"),
    )
    extract_model_config: Mapped[dict[str, JsonValue] | None] = mapped_column(sa.JSON, nullable=True, default=None)
    graph_version: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default="v1", server_default=sa.text("'v1'")
    )

    @property
    def is_graph_indexing_enabled(self) -> bool:
        """返回是否应执行图谱抽取，并兼容历史混合配置。"""
        if self.index_enabled is not None:
            return self.index_enabled
        return self.enabled and self.extract_model_config is not None
