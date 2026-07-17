"""Graph Index Reconciler。

职责
====

- 在全局开关与定时任务开关均开启时，定期把 completed Document 登记为 Graph
  Index Job。
- 恢复超时的 running/retry_waiting Job，避免永久卡住。
- 多次扫描幂等：通过 Job 唯一键保证。
- 事务边界由本模块管理：每批一个事务，事务提交后再投递 Worker。

边界
====

- 不实现 Graph 抽取与 Neo4j 写入（Phase 3）。
- 不修改普通索引任务，不影响既有 Dataset 检索行为。
- 只在 `GRAPH_RAG_ENABLED` 与 `ENABLE_GRAPH_RECONCILE_TASK` 均为真时执行实际
  扫描。
"""

from __future__ import annotations

import logging

from configs import dify_config
from core.rag.graph_indexing.coordinator import GraphIndexDispatcher, GraphIndexJobCoordinator
from core.rag.graph_indexing.entities import GRAPH_INDEX_STALE_MINUTES, GRAPH_RECONCILE_BATCH_SIZE
from core.rag.graph_indexing.repositories import GraphIndexJobRepository

logger = logging.getLogger(__name__)


class GraphIndexReconciler:
    """对账 Graph Index Job 的编排器。"""

    def __init__(
        self,
        repository: GraphIndexJobRepository,
        coordinator: GraphIndexJobCoordinator,
    ) -> None:
        self._repository = repository
        self._coordinator = coordinator

    def reconcile(self) -> None:
        """执行一轮对账。开关关闭时直接返回。"""
        if not self._is_enabled():
            logger.debug("graph_index reconcile skipped: global flag disabled")
            return

        recovered = self._repository.requeue_stale_running_jobs(stale_minutes=GRAPH_INDEX_STALE_MINUTES)
        if recovered:
            logger.info("graph_index requeued stale jobs count=%s", recovered)

        offset = 0
        while True:
            configs = self._repository.list_enabled_graph_configs(limit=GRAPH_RECONCILE_BATCH_SIZE, offset=offset)
            if not configs:
                break
            for config in configs:
                self._reconcile_dataset(config)
            offset += len(configs)
            if len(configs) < GRAPH_RECONCILE_BATCH_SIZE:
                break

    def _reconcile_dataset(self, config) -> None:
        dataset_id = config.dataset_id
        tenant_id = config.tenant_id
        graph_version = config.graph_version
        doc_offset = 0
        while True:
            documents = self._repository.list_completed_documents(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                limit=GRAPH_RECONCILE_BATCH_SIZE,
                offset=doc_offset,
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
                    )
                except Exception:
                    logger.exception(
                        "graph_index reconcile failed tenant=%s dataset=%s document=%s",
                        tenant_id,
                        dataset_id,
                        getattr(document, "id", None),
                    )
            doc_offset += len(documents)
            if len(documents) < GRAPH_RECONCILE_BATCH_SIZE:
                break

    def _is_enabled(self) -> bool:
        return bool(getattr(dify_config, "GRAPH_RAG_ENABLED", False)) and bool(
            getattr(dify_config, "ENABLE_GRAPH_RECONCILE_TASK", False)
        )


__all__ = ["GraphIndexDispatcher", "GraphIndexReconciler"]
