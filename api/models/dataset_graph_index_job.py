"""GraphRAG 索引 Job 持久化模型。

该表刻意独立于 Dify 上游 Dataset/Document 模型，新增独立表
`dataset_graph_index_jobs`，便于后续 upstream 升级时只保留本表与其迁移。

不变量
======

- 唯一键 `(dataset_id, document_id, index_node_id, source_version, graph_version)`
  保证同一文档、图谱 scope、内容版本和图版本只存在一个 Job，Reconciler 多次
  扫描幂等。
- `tenant_id` 与 `dataset_id`/`document_id` 一同存储，便于按租户隔离查询，
  避免跨租户扫描。
- Phase 2 不在此模型中记录 Graph 抽取产物，Phase 3 通过 Adapter 写入图数据库，
  失败信息记录在 `last_error_code`/`last_error_message`。
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from core.rag.graph_indexing.entities import DATASET_GRAPH_INDEX_SCOPE, GraphIndexJobStatus
from libs.datetime_utils import naive_utc_now

from .base import DefaultFieldsDCMixin, TypeBase
from .types import EnumText, StringUUID


class DatasetGraphIndexJob(DefaultFieldsDCMixin, TypeBase):
    """表示一次待执行或已执行的 Graph Index 任务。"""

    __tablename__ = "dataset_graph_index_jobs"
    __table_args__ = (
        sa.PrimaryKeyConstraint("id", name="dataset_graph_index_job_pkey"),
        sa.UniqueConstraint(
            "dataset_id",
            "document_id",
            "index_node_id",
            "source_version",
            "graph_version",
            name="dataset_graph_index_job_unique",
        ),
        sa.Index("dataset_graph_index_job_status_available_idx", "status", "available_at"),
        sa.Index("dataset_graph_index_job_dataset_document_idx", "dataset_id", "document_id"),
        sa.Index("dataset_graph_index_job_scope_idx", "dataset_id", "index_node_id"),
        sa.Index("dataset_graph_index_job_tenant_idx", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    dataset_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    document_id: Mapped[str] = mapped_column(StringUUID, nullable=False)
    source_version: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    graph_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    index_node_id: Mapped[str] = mapped_column(
        sa.String(255),
        nullable=False,
        default=DATASET_GRAPH_INDEX_SCOPE,
        server_default=sa.text("'__dataset__'"),
    )
    status: Mapped[GraphIndexJobStatus] = mapped_column(
        EnumText(GraphIndexJobStatus, length=32),
        nullable=False,
        default=GraphIndexJobStatus.PENDING,
        server_default=sa.text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0, server_default=sa.text("0"))
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime,
        nullable=False,
        default_factory=naive_utc_now,
        insert_default=naive_utc_now,
        server_default=sa.func.current_timestamp(),
    )
    locked_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True, default=None)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime, nullable=True, default=None)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(128), nullable=True, default=None)
    last_error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True, default=None)
