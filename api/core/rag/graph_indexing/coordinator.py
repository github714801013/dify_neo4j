"""Graph Index Job 协调器。

职责
====

- 连接 Reconciler 与 Repository：计算 source_version、幂等创建 Job。
- 不直接访问数据库以外的状态；事务边界由 Reconciler 通过 session 控制。
- 不负责投递 Celery。调用方必须在数据库事务提交后派发可执行 Job。

边界
====

- Coordinator 不负责扫描 Document；扫描由 Reconciler 完成。
- Coordinator 不负责 Graph 抽取；Worker 在 Phase 3 接入适配器。
"""

from __future__ import annotations

import logging
from collections.abc import Collection

from core.rag.graph_indexing.entities import DATASET_GRAPH_INDEX_SCOPE
from core.rag.graph_indexing.repositories import GraphIndexJobRepository
from core.rag.graph_indexing.versioning import (
    build_segment_source_facts,
    build_source_facts,
    compute_source_version,
)
from models.dataset import Document

logger = logging.getLogger(__name__)


class GraphIndexJobCoordinator:
    """协调 Graph Index Job 的版本计算与幂等创建。"""

    def __init__(self, repository: GraphIndexJobRepository) -> None:
        self._repository = repository

    def ensure_job_for_document(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document: Document,
        graph_version: str,
        index_node_id: str = DATASET_GRAPH_INDEX_SCOPE,
        excluded_index_node_ids: Collection[str] = (),
    ) -> str | None:
        """为单个 Document 幂等创建 Job。

        返回新建 Job 的 id；若已存在则返回 None。Celery 派发必须由调用方在
        数据库事务提交后执行，防止 Worker 先于 Job 可见。
        """
        facts = build_source_facts(
            document.id,
            updated_at=document.updated_at,
            completed_at=document.completed_at,
            batch=document.batch,
            word_count=document.word_count,
        )
        segments = self._repository.list_active_segments_for_document(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            index_node_id=index_node_id,
            excluded_index_node_ids=excluded_index_node_ids,
        )
        segment_facts = [
            build_segment_source_facts(
                segment.id,
                content=segment.content or "",
                updated_at=segment.updated_at,
            )
            for segment in segments
        ]
        source_version = compute_source_version(facts, segment_facts)
        job = self._repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version=source_version,
            graph_version=graph_version,
            index_node_id=index_node_id,
        )
        if job is None:
            return None
        logger.info(
            "graph_index_job created tenant=%s dataset=%s document=%s scope=%s job=%s",
            tenant_id,
            dataset_id,
            document.id,
            index_node_id,
            job.id,
        )
        return job.id


__all__ = ["GraphIndexJobCoordinator"]
