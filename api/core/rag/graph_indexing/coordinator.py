"""Graph Index Job 协调器。

职责
====

- 连接 Reconciler 与 Repository：计算 source_version、幂等创建 Job、决定是否
  投递 Worker 任务。
- 不直接访问数据库以外的状态；事务边界由 Reconciler 通过 session 控制。
- 投递 Worker 任务通过注入的 `dispatch` 回调，避免直接依赖 Celery，便于测试。

边界
====

- Coordinator 不负责扫描 Document；扫描由 Reconciler 完成。
- Coordinator 不负责 Graph 抽取；Worker 在 Phase 3 接入适配器。
"""

from __future__ import annotations

import logging
from typing import Protocol

from core.rag.graph_indexing.repositories import GraphIndexJobRepository
from core.rag.graph_indexing.versioning import (
    build_source_facts,
    compute_source_version,
)
from models.dataset import Document

logger = logging.getLogger(__name__)


class GraphIndexDispatcher(Protocol):
    """投递 Graph Index Worker 任务的回调协议。"""

    def __call__(self, job_id: str) -> None:
        """将 job_id 投递到 graph_index 队列。失败应记录日志但不抛出。"""
        ...


class GraphIndexJobCoordinator:
    """协调 Graph Index Job 的幂等创建与 Worker 投递。"""

    def __init__(
        self,
        repository: GraphIndexJobRepository,
        dispatcher: GraphIndexDispatcher,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher

    def ensure_job_for_document(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document: Document,
        graph_version: str,
    ) -> str | None:
        """为单个 Document 幂等创建 Job 并在事务内投递 Worker 任务。

        返回新建 Job 的 id；若已存在则返回 None。调用方负责在事务提交后再让
        Worker 真正消费，本方法只负责在同一事务内登记 Job 并尝试投递。
        """
        facts = build_source_facts(
            document.id,
            updated_at=document.updated_at,
            completed_at=document.completed_at,
            batch=document.batch,
            word_count=document.word_count,
        )
        source_version = compute_source_version(facts)
        job = self._repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version=source_version,
            graph_version=graph_version,
        )
        if job is None:
            return None
        logger.info(
            "graph_index_job created tenant=%s dataset=%s document=%s job=%s",
            tenant_id,
            dataset_id,
            document.id,
            job.id,
        )
        try:
            self._dispatcher(job.id)
        except Exception:
            # 投递失败不破坏事务；Reconciler 下一轮或 stale 恢复会重新调度。
            logger.exception("failed to dispatch graph_index job=%s", job.id)
        return job.id


__all__ = ["GraphIndexDispatcher", "GraphIndexJobCoordinator"]
