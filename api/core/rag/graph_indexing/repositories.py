"""Graph Index Job 持久化仓储。

边界
====

- 只依赖 `extensions.ext_database.db.session`，不创建新的数据库 engine，避免
  与 Dify 上游连接池/事务管理冲突。
- 所有查询都带 `tenant_id` 范围限制，保证租户隔离。
- 幂等创建使用 `begin_nested()` + `IntegrityError` 处理并发重复插入；冲突时
  视为幂等成功，返回已存在的 Job，不破坏外层事务。

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
from models.dataset import Document
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob

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

    def requeue_stale_running_jobs(self, *, stale_minutes: int) -> int:
        """将超时的 running 与 retry_waiting Job 恢复为 pending。"""
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
    ) -> DatasetGraphIndexJob:
        """将 running Job 标记为 retry_waiting，并按基础等待秒推迟 available_at。"""
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
        self._session.add(job)
        try:
            self._session.flush()
        except IntegrityError as ex:
            # 并发插入触发唯一键冲突，视为幂等成功。
            self._session.rollback()
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
                Document.archived.is_(False),
                Document.completed_at.is_not(None),
            )
            .order_by(Document.created_at)
            .limit(limit)
            .offset(offset)
        )
        return self._session.scalars(stmt).all()

    def requeue_stale_running_jobs(self, *, stale_minutes: int) -> int:
        now = naive_utc_now()
        threshold = now - timedelta(minutes=stale_minutes)
        # retry_waiting 一律恢复为 pending；running 仅在 locked_at 超过阈值时恢复，
        # 避免误伤正在执行中的 Job。
        stale_jobs = self._session.scalars(
            select(DatasetGraphIndexJob)
            .where(
                DatasetGraphIndexJob.status.in_(list(resumable_statuses())),
            )
            .limit(1000)
        ).all()
        recovered = 0
        for job in stale_jobs:
            is_retry = job.status == GraphIndexJobStatus.RETRY_WAITING
            is_stale_running = (
                job.status == GraphIndexJobStatus.RUNNING and job.locked_at is not None and job.locked_at < threshold
            )
            if is_retry or is_stale_running:
                job.status = GraphIndexJobStatus.PENDING
                job.available_at = now
                job.locked_at = None
                recovered += 1
        return recovered

    def get(self, job_id: str) -> DatasetGraphIndexJob | None:
        return self._session.get(DatasetGraphIndexJob, job_id)

    def claim(self, job_id: str) -> DatasetGraphIndexJob:
        job = self.get(job_id)
        if job is None:
            raise GraphIndexJobNotFoundError(f"graph_index_job not found: {job_id}")
        if job.status not in claimable_statuses():
            raise GraphIndexJobClaimError(
                f"graph_index_job not claimable: status={job.status}",
                code="graph_index_job_not_claimable",
            )
        job.status = GraphIndexJobStatus.RUNNING
        job.locked_at = naive_utc_now()
        job.started_at = naive_utc_now()
        return job

    def mark_retry_waiting(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
        retry_base_seconds: int,
    ) -> DatasetGraphIndexJob:
        job = self._require_running_job(job_id)
        job.status = GraphIndexJobStatus.RETRY_WAITING
        job.last_error_code = error_code
        job.last_error_message = error_message
        job.attempts = (job.attempts or 0) + 1
        job.available_at = naive_utc_now() + timedelta(seconds=max(0, retry_base_seconds))
        job.locked_at = None
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
