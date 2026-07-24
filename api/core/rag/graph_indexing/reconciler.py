"""Graph Index Reconciler。

职责
====

- 在全局开关与定时任务开关均开启时，定期把 completed Document 登记为 Graph
  Index Job。
- 恢复超时的 running/retry_waiting Job，避免永久卡住。
- 多次扫描幂等：通过 Job 唯一键保证。
- 返回当前可派发 Job ID；调用方必须在事务提交后再投递 Worker。

边界
====

- 不实现 Graph 抽取与 Neo4j 写入（Phase 3）。
- 不修改普通索引任务，不影响既有 Dataset 检索行为。
- 只在 `GRAPH_RAG_ENABLED` 与 `ENABLE_GRAPH_RECONCILE_TASK` 均为真时执行实际
  扫描。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from configs import dify_config
from core.rag.graph_indexing.coordinator import GraphIndexJobCoordinator
from core.rag.graph_indexing.entities import (
    DATASET_GRAPH_INDEX_SCOPE,
    GRAPH_INDEX_STALE_MINUTES,
    GRAPH_RECONCILE_BATCH_SIZE,
)
from core.rag.graph_indexing.repositories import GraphIndexJobRepository
from core.rag.graph_indexing.scopes import PublishedNodeGraphScope, list_published_node_graph_scopes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphDatasetCleanupCommand:
    """数据库事务提交后执行的 Dataset 图生命周期对账命令。"""

    tenant_id: str
    dataset_id: str
    index_node_id: str
    active_document_ids: tuple[str, ...]
    active_segment_ids: tuple[str, ...]


@dataclass(frozen=True)
class GraphReconcilePlan:
    """一次 Reconciler 扫描产生的提交后动作。"""

    job_ids: tuple[str, ...]
    cleanup_commands: tuple[GraphDatasetCleanupCommand, ...]


class GraphIndexReconciler:
    """对账 Graph Index Job 的编排器。"""

    def __init__(
        self,
        repository: GraphIndexJobRepository,
        coordinator: GraphIndexJobCoordinator,
    ) -> None:
        self._repository = repository
        self._coordinator = coordinator

    def reconcile(self) -> list[str]:
        """兼容调用方：执行对账并只返回应派发的 Job ID。"""
        return list(self.reconcile_plan().job_ids)

    def reconcile_plan(self) -> GraphReconcilePlan:
        """执行一轮对账，返回事务提交后应执行的全部动作。"""
        if not self._is_enabled():
            logger.debug("graph_index reconcile skipped: global flag disabled")
            return GraphReconcilePlan(job_ids=(), cleanup_commands=())

        recovered = self._repository.requeue_stale_running_jobs(stale_minutes=GRAPH_INDEX_STALE_MINUTES)
        if recovered:
            logger.info("graph_index requeued stale jobs count=%s", recovered)

        node_scopes = list_published_node_graph_scopes(self._repository.session)
        node_scope_ids_by_dataset = _group_node_scope_ids(node_scopes)
        cleanup_commands = [self._reconcile_node_scope(scope) for scope in node_scopes]

        offset = 0
        while True:
            configs = self._repository.list_enabled_graph_configs(limit=GRAPH_RECONCILE_BATCH_SIZE, offset=offset)
            if not configs:
                break
            for config in configs:
                cleanup_commands.append(
                    self._reconcile_dataset_scope(
                        config,
                        excluded_index_node_ids=node_scope_ids_by_dataset.get(
                            (config.tenant_id, config.dataset_id), frozenset()
                        ),
                    )
                )
            offset += len(configs)
            if len(configs) < GRAPH_RECONCILE_BATCH_SIZE:
                break

        dispatchable_jobs = self._repository.list_dispatchable_jobs(limit=GRAPH_RECONCILE_BATCH_SIZE)
        return GraphReconcilePlan(
            job_ids=tuple(job.id for job in dispatchable_jobs),
            cleanup_commands=tuple(cleanup_commands),
        )

    def _reconcile_node_scope(self, scope: PublishedNodeGraphScope) -> GraphDatasetCleanupCommand:
        return self._reconcile_scope(
            tenant_id=scope.tenant_id,
            dataset_id=scope.dataset_id,
            graph_version=scope.graph_version,
            index_node_id=scope.index_node_id,
        )

    def _reconcile_dataset_scope(
        self, config, *, excluded_index_node_ids: frozenset[str]
    ) -> GraphDatasetCleanupCommand:
        return self._reconcile_scope(
            tenant_id=config.tenant_id,
            dataset_id=config.dataset_id,
            graph_version=config.graph_version,
            index_node_id=DATASET_GRAPH_INDEX_SCOPE,
            excluded_index_node_ids=excluded_index_node_ids,
        )

    def _reconcile_scope(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        graph_version: str,
        index_node_id: str,
        excluded_index_node_ids: frozenset[str] = frozenset(),
    ) -> GraphDatasetCleanupCommand:
        doc_offset = 0
        while True:
            documents = self._repository.list_completed_documents(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                limit=GRAPH_RECONCILE_BATCH_SIZE,
                offset=doc_offset,
                index_node_id=index_node_id,
                excluded_index_node_ids=excluded_index_node_ids,
            )
            if not documents:
                break
            for document in documents:
                try:
                    self._coordinator.ensure_job_for_document(
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        document=document,
                        graph_version=graph_version,
                        index_node_id=index_node_id,
                        excluded_index_node_ids=excluded_index_node_ids,
                    )
                except Exception:
                    logger.exception(
                        "graph_index reconcile failed tenant=%s dataset=%s scope=%s document=%s",
                        tenant_id,
                        dataset_id,
                        index_node_id,
                        getattr(document, "id", None),
                    )
            doc_offset += len(documents)
            if len(documents) < GRAPH_RECONCILE_BATCH_SIZE:
                break

        return GraphDatasetCleanupCommand(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            index_node_id=index_node_id,
            active_document_ids=tuple(
                self._repository.list_active_document_ids(tenant_id=tenant_id, dataset_id=dataset_id)
            ),
            active_segment_ids=tuple(
                self._repository.list_active_segment_ids(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    index_node_id=index_node_id,
                    excluded_index_node_ids=excluded_index_node_ids,
                )
            ),
        )

    def _is_enabled(self) -> bool:
        return bool(getattr(dify_config, "GRAPH_RAG_ENABLED", False)) and bool(
            getattr(dify_config, "ENABLE_GRAPH_RECONCILE_TASK", False)
        )


def _group_node_scope_ids(scopes: list[PublishedNodeGraphScope]) -> dict[tuple[str, str], frozenset[str]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for scope in scopes:
        grouped.setdefault((scope.tenant_id, scope.dataset_id), set()).add(scope.index_node_id)
    return {key: frozenset(index_node_ids) for key, index_node_ids in grouped.items()}


__all__ = [
    "GraphDatasetCleanupCommand",
    "GraphIndexReconciler",
    "GraphReconcilePlan",
]
