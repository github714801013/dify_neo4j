"""GraphRAG 索引 Job 的领域实体与状态机。

边界
====

- 本模块只描述 Graph Index Job 的数据契约与状态迁移规则，不直接访问数据库；
  持久化由 `repositories.py` 负责。
- 状态迁移规则集中在本模块，便于在 Reconciler/Worker/测试中复用与校验。
- Phase 2 不实现实体关系抽取与 Neo4j 写入，因此 `succeeded` 仅在 Phase 3
  适配器真正写入图数据后才应被 Worker 标记。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

# 批量扫描 Document 与 Job 的默认步长。Phase 2 保持单一常量，避免过早引入配置。
GRAPH_RECONCILE_BATCH_SIZE = 100

# 超时 running Job 恢复为 pending 的默认阈值（分钟）。Reconciler 每轮都会执行
# 恢复，该阈值决定多久未心跳的 running Job 视为 stale。
GRAPH_INDEX_STALE_MINUTES = 30


class GraphIndexJobStatus(StrEnum):
    """Graph Index Job 的状态机取值。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    RETRY_WAITING = "retry_waiting"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal(cls) -> frozenset[GraphIndexJobStatus]:
        """返回终态集合：终态 Job 不再被 Reconciler 自动恢复或重试。"""
        return frozenset({cls.SUCCEEDED, cls.FAILED, cls.CANCELLED})


# 允许的迁移：键是当前状态，值是该状态允许迁移到的目标状态集合。
_ALLOWED_TRANSITIONS: Mapping[GraphIndexJobStatus, frozenset[GraphIndexJobStatus]] = {
    GraphIndexJobStatus.PENDING: frozenset({GraphIndexJobStatus.RUNNING, GraphIndexJobStatus.CANCELLED}),
    GraphIndexJobStatus.RUNNING: frozenset(
        {
            GraphIndexJobStatus.SUCCEEDED,
            GraphIndexJobStatus.RETRY_WAITING,
            GraphIndexJobStatus.FAILED,
            GraphIndexJobStatus.PENDING,
            GraphIndexJobStatus.CANCELLED,
        }
    ),
    GraphIndexJobStatus.RETRY_WAITING: frozenset({GraphIndexJobStatus.PENDING, GraphIndexJobStatus.CANCELLED}),
    GraphIndexJobStatus.SUCCEEDED: frozenset(),
    GraphIndexJobStatus.FAILED: frozenset({GraphIndexJobStatus.PENDING, GraphIndexJobStatus.CANCELLED}),
    GraphIndexJobStatus.CANCELLED: frozenset(),
}


def is_transition_allowed(
    current: GraphIndexJobStatus,
    target: GraphIndexJobStatus,
) -> bool:
    """判断状态迁移是否被状态机允许。"""
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def claimable_statuses() -> frozenset[GraphIndexJobStatus]:
    """Worker 可以原子领取的状态集合。"""
    return frozenset({GraphIndexJobStatus.PENDING})


def resumable_statuses() -> frozenset[GraphIndexJobStatus]:
    """Reconciler 可以自动恢复为 pending 的状态集合。

    超时的 running 与等待重试的 retry_waiting 都允许恢复，避免 Job 永久卡住。
    """
    return frozenset({GraphIndexJobStatus.RUNNING, GraphIndexJobStatus.RETRY_WAITING})


__all__ = (
    "GRAPH_INDEX_STALE_MINUTES",
    "GRAPH_RECONCILE_BATCH_SIZE",
    "GraphIndexJobStatus",
    "claimable_statuses",
    "is_transition_allowed",
    "resumable_statuses",
)
