"""Graph Index Job 持久化仓储。

边界
====

- 只依赖 `extensions.ext_database.db.session`，不创建新的数据库 engine，避免
  与 Dify 上游连接池/事务管理冲突。
- 所有查询都带 `tenant_id` 范围限制，保证租户隔离。
- 幂等创建使用 `begin_nested()` + `IntegrityError` 处理并发重复插入；冲突时
  视为幂等成功，返回已存在的 Job，不破坏外层事务。
- Job 领取同时锁定 Document 行，保证同一文档的不同 source_version 不会并行
  写入和切换 Neo4j 版本。

为什么不直接在 Reconciler 里写 SQL
------------------------------------

将持久化逻辑集中在本模块，便于：
- 单元测试用 in-memory SQLite 覆盖幂等与状态迁移；
- 后续 Phase 3 适配器可复用同一套 Job 读写接口；
- 避免业务编排逻辑与 SQLAlchemy 细节耦合。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.rag.graph_indexing.entities import (
    GraphIndexJobStatus,
    claimable_statuses,
    resumable_statuses,
)
from core.rag.graph_indexing.errors import (
    GraphIndexJobClaimError,
    GraphIndexJobConflictError,
    GraphIndexJobNotFoundError,
)
from libs.datetime_utils import naive_utc_now
from models.dataset import Document, DocumentSegment
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob
from models.enums import SegmentStatus

logger = logging.getLogger(__name__)


class GraphIndexJobRepository(Protocol):
    """Graph Index Job 仓储协议。"""

    def create_if_missing(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        source_version: str,
        graph_version: str,
    ) -> DatasetGraphIndexJob | None:
        """幂等创建 Job。若唯一键已存在则返回 None，否则返回新建的 Job。"""
        ...

    def list_enabled_graph_configs(
        self,
        *,
        tenant_id: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> Sequence[DatasetGraphConfig]:
        """列出 enabled 的 GraphRAG 配置。"""
        ...

    def list_completed_documents(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        limit: int,
        offset: int,
    ) -> Sequence[Document]:
        """列出 completed 且未归档的 Document。"""
        ...

    def list_active_segments_for_document(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
    ) -> Sequence[DocumentSegment]:
        """列出参与当前图版本计算和写入的有效 Segment。"""
        ...

    def list_active_document_ids(self, *, tenant_id: str, dataset_id: str) -> Sequence[str]:
        """列出 Dataset 当前仍有效的 Document ID。"""
        ...

    def list_active_segment_ids(self, *, tenant_id: str, dataset_id: str) -> Sequence[str]:
        """列出 Dataset 当前仍有效的 Segment ID。"""
        ...

    def requeue_stale_running_jobs(self, *, stale_minutes: int) -> int:
        """将超时 running 与已到期 retry_waiting Job 恢复为 pending。"""
        ...

    def list_dispatchable_jobs(self, *, limit: int) -> Sequence[DatasetGraphIndexJob]:
        """列出当前可投递的 pending Job。"""
        ...

    def get(self, job_id: str) -> DatasetGraphIndexJob | None:
        """按 ID 获取 Job。"""
        ...

    def claim(self, job_id: str) -> DatasetGraphIndexJob:
        """原子领取 pending Job 为 running；失败抛 GraphIndexJobClaimError。"""
        ...

    def mark_retry_waiting(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
        retry_base_seconds: int,
        max_retries: int,
    ) -> DatasetGraphIndexJob:
        """记录一次失败；未达上限时指数退避，达到上限时进入 failed。"""
        ...

    def mark_failed(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> DatasetGraphIndexJob:
        """将 Job 标记为 failed。"""
        ...

    def mark_cancelled(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> DatasetGraphIndexJob:
        """将已过期或不再需要执行的 running Job 标记为 cancelled。"""
        ...

    def mark_succeeded(self, job_id: str) -> DatasetGraphIndexJob:
        """将 Job 标记为 succeeded。Phase 3 适配器写入成功后调用。"""
        ...


class SqlAlchemyGraphIndexJobRepository(GraphIndexJobRepository):
    """基于 `db.session` 的 Graph Index Job 仓储实现。

    仓储本身不开启/提交顶层事务；事务边界由调用方（Reconciler/Worker 任务）
    通过 `session.begin()` 控制。`begin_nested()` 仅用于幂等创建的并发保护。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        """暴露底层 session，便于调用方在同一事务内执行额外操作。"""
        return self._session

    def create_if_missing(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        source_version: str,
        graph_version: str,
    ) -> DatasetGraphIndexJob | None:
        existing = self._session.scalar(
            select(DatasetGraphIndexJob)
            .where(
                DatasetGraphIndexJob.tenant_id == tenant_id,
                DatasetGraphIndexJob.dataset_id == dataset_id,
                DatasetGraphIndexJob.document_id == document_id,
                DatasetGraphIndexJob.source_version == source_version,
                DatasetGraphIndexJob.graph_version == graph_version,
            )
            .limit(1)
        )
        if existing is not None:
            logger.debug(
                "graph_index_job already exists tenant=%s dataset=%s document=%s",
                tenant_id,
                dataset_id,
                document_id,
            )
            return None

        job = DatasetGraphIndexJob(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            source_version=source_version,
            graph_version=graph_version,
            status=GraphIndexJobStatus.PENDING,
        )
        try:
            # 唯一键冲突只回滚 savepoint，不能破坏 Reconciler 的外层事务。
            with self._session.begin_nested():
                self._session.add(job)
                self._session.flush()
        except IntegrityError as ex:
            self._session.expire_all()
            concurrent_job = self._session.scalar(
                select(DatasetGraphIndexJob)
                .where(
                    DatasetGraphIndexJob.tenant_id == tenant_id,
                    DatasetGraphIndexJob.dataset_id == dataset_id,
                    DatasetGraphIndexJob.document_id == document_id,
                    DatasetGraphIndexJob.source_version == source_version,
                    DatasetGraphIndexJob.graph_version == graph_version,
                )
                .limit(1)
            )
            if concurrent_job is not None:
                return None
            raise GraphIndexJobConflictError(
                "graph_index_job unique conflict",
                code="graph_index_job_conflict",
            ) from ex
        return job

    def list_enabled_graph_configs(
        self,
        *,
        tenant_id: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> Sequence[DatasetGraphConfig]:
        stmt = (
            select(DatasetGraphConfig)
            .where(DatasetGraphConfig.enabled.is_(True))
            .order_by(DatasetGraphConfig.created_at)
            .limit(limit)
            .offset(offset)
        )
        if tenant_id is not None:
            stmt = stmt.where(DatasetGraphConfig.tenant_id == tenant_id)
        return self._session.scalars(stmt).all()

    def list_completed_documents(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        limit: int,
        offset: int,
    ) -> Sequence[Document]:
        stmt = (
            select(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.dataset_id == dataset_id,
                Document.enabled.is_(True),
                Document.archived.is_(False),
                Document.indexing_status == "completed",
                Document.completed_at.is_not(None),
            )
            .order_by(Document.created_at)
            .limit(limit)
            .offset(offset)
        )
        return self._session.scalars(stmt).all()

    def list_active_segments_for_document(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
    ) -> Sequence[DocumentSegment]:
        return self._session.scalars(
            select(DocumentSegment)
            .where(
                DocumentSegment.tenant_id == tenant_id,
                DocumentSegment.dataset_id == dataset_id,
                DocumentSegment.document_id == document_id,
                DocumentSegment.enabled.is_(True),
                DocumentSegment.status == SegmentStatus.COMPLETED,
            )
            .order_by(DocumentSegment.position, DocumentSegment.id)
        ).all()

    def list_active_document_ids(self, *, tenant_id: str, dataset_id: str) -> Sequence[str]:
        return self._session.scalars(
            select(Document.id).where(
                Document.tenant_id == tenant_id,
                Document.dataset_id == dataset_id,
                Document.enabled.is_(True),
                Document.archived.is_(False),
                Document.indexing_status == "completed",
                Document.completed_at.is_not(None),
            )
        ).all()

    def list_active_segment_ids(self, *, tenant_id: str, dataset_id: str) -> Sequence[str]:
        return self._session.scalars(
            select(DocumentSegment.id)
            .join(Document, Document.id == DocumentSegment.document_id)
            .where(
                DocumentSegment.tenant_id == tenant_id,
                DocumentSegment.dataset_id == dataset_id,
                DocumentSegment.enabled.is_(True),
                DocumentSegment.status == SegmentStatus.COMPLETED,
                Document.tenant_id == tenant_id,
                Document.dataset_id == dataset_id,
                Document.enabled.is_(True),
                Document.archived.is_(False),
                Document.indexing_status == "completed",
                Document.completed_at.is_not(None),
            )
        ).all()

    def requeue_stale_running_jobs(self, *, stale_minutes: int) -> int:
        now = naive_utc_now()
        threshold = now - timedelta(minutes=stale_minutes)
        recoverable_jobs = self._session.scalars(
            select(DatasetGraphIndexJob).where(DatasetGraphIndexJob.status.in_(list(resumable_statuses()))).limit(1000)
        ).all()
        recovered = 0
        for job in recoverable_jobs:
            is_retry_due = job.status == GraphIndexJobStatus.RETRY_WAITING and job.available_at <= now
            is_stale_running = (
                job.status == GraphIndexJobStatus.RUNNING and job.locked_at is not None and job.locked_at < threshold
            )
            if is_retry_due or is_stale_running:
                job.status = GraphIndexJobStatus.PENDING
                job.available_at = now
                job.locked_at = None
                recovered += 1
        return recovered

    def list_dispatchable_jobs(self, *, limit: int) -> Sequence[DatasetGraphIndexJob]:
        now = naive_utc_now()
        return self._session.scalars(
            select(DatasetGraphIndexJob)
            .join(
                DatasetGraphConfig,
                (DatasetGraphConfig.tenant_id == DatasetGraphIndexJob.tenant_id)
                & (DatasetGraphConfig.dataset_id == DatasetGraphIndexJob.dataset_id),
            )
            .where(
                DatasetGraphIndexJob.status == GraphIndexJobStatus.PENDING,
                DatasetGraphIndexJob.available_at <= now,
                DatasetGraphConfig.enabled.is_(True),
                DatasetGraphConfig.graph_version == DatasetGraphIndexJob.graph_version,
            )
            .order_by(DatasetGraphIndexJob.available_at, DatasetGraphIndexJob.created_at)
            .limit(limit)
        ).all()

    def get(self, job_id: str) -> DatasetGraphIndexJob | None:
        return self._session.get(DatasetGraphIndexJob, job_id)

    def claim(self, job_id: str) -> DatasetGraphIndexJob:
        """领取 Job，并通过 Document 行锁串行化同一文档的不同 source_version。"""
        now = naive_utc_now()
        job = self._session.scalar(
            select(DatasetGraphIndexJob)
            .where(
                DatasetGraphIndexJob.id == job_id,
                DatasetGraphIndexJob.status.in_(list(claimable_statuses())),
                DatasetGraphIndexJob.available_at <= now,
            )
            .with_for_update()
        )
        if job is None:
            existing = self.get(job_id)
            if existing is None:
                raise GraphIndexJobNotFoundError(f"graph_index_job not found: {job_id}")
            raise GraphIndexJobClaimError(
                f"graph_index_job not claimable: status={existing.status}",
                code="graph_index_job_not_claimable",
            )

        # 不同 source_version 会形成不同 Job 行，仅锁 Job 自身无法阻止并发写同一
        # 文档。额外锁 Document 行，使同文档的领取检查在 PostgreSQL/MySQL 中串行。
        document_id = self._session.scalar(
            select(Document.id)
            .where(
                Document.id == job.document_id,
                Document.tenant_id == job.tenant_id,
                Document.dataset_id == job.dataset_id,
            )
            .with_for_update()
        )
        if document_id is None:
            raise GraphIndexJobClaimError(
                f"graph_index document missing: document_id={job.document_id}",
                code="graph_index_document_missing",
            )

        running_job_id = self._session.scalar(
            select(DatasetGraphIndexJob.id)
            .where(
                DatasetGraphIndexJob.tenant_id == job.tenant_id,
                DatasetGraphIndexJob.dataset_id == job.dataset_id,
                DatasetGraphIndexJob.document_id == job.document_id,
                DatasetGraphIndexJob.status == GraphIndexJobStatus.RUNNING,
                DatasetGraphIndexJob.id != job.id,
            )
            .limit(1)
        )
        if running_job_id is not None:
            raise GraphIndexJobClaimError(
                f"graph_index document already running: job={running_job_id}",
                code="graph_index_document_busy",
            )

        job.status = GraphIndexJobStatus.RUNNING
        job.locked_at = now
        job.started_at = now
        return job

    def mark_retry_waiting(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
        retry_base_seconds: int,
        max_retries: int,
    ) -> DatasetGraphIndexJob:
        job = self._require_running_job(job_id)
        next_attempt = (job.attempts or 0) + 1
        now = naive_utc_now()
        job.last_error_code = error_code
        job.last_error_message = error_message
        job.attempts = next_attempt
        job.locked_at = None
        if next_attempt > max(0, max_retries):
            job.status = GraphIndexJobStatus.FAILED
            job.completed_at = now
            return job

        delay_seconds = max(0, retry_base_seconds) * (2 ** (next_attempt - 1))
        job.status = GraphIndexJobStatus.RETRY_WAITING
        job.available_at = now + timedelta(seconds=delay_seconds)
        return job

    def mark_failed(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> DatasetGraphIndexJob:
        job = self._require_running_job(job_id)
        job.status = GraphIndexJobStatus.FAILED
        job.last_error_code = error_code
        job.last_error_message = error_message
        job.completed_at = naive_utc_now()
        job.locked_at = None
        return job

    def mark_cancelled(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> DatasetGraphIndexJob:
        job = self._require_running_job(job_id)
        job.status = GraphIndexJobStatus.CANCELLED
        job.last_error_code = error_code
        job.last_error_message = error_message
        job.completed_at = naive_utc_now()
        job.locked_at = None
        return job

    def mark_succeeded(self, job_id: str) -> DatasetGraphIndexJob:
        job = self._require_running_job(job_id)
        job.status = GraphIndexJobStatus.SUCCEEDED
        job.completed_at = naive_utc_now()
        job.locked_at = None
        return job

    def _require_running_job(self, job_id: str) -> DatasetGraphIndexJob:
        job = self.get(job_id)
        if job is None:
            raise GraphIndexJobNotFoundError(f"graph_index_job not found: {job_id}")
        if job.status != GraphIndexJobStatus.RUNNING:
            raise GraphIndexJobClaimError(
                f"graph_index_job not running: status={job.status}",
                code="graph_index_job_not_running",
            )
        return job


__all__ = ["GraphIndexJobRepository", "SqlAlchemyGraphIndexJobRepository"]
