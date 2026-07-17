"""GraphRAG 对账定时任务。

该任务由 Celery Beat 按 `GRAPH_RECONCILE_INTERVAL_MINUTES` 触发，调用
`GraphIndexReconciler` 把 completed Document 登记为 Graph Index Job。

边界
====

- 任务幂等：多次执行安全。
- 失败仅记录日志并重试，不破坏普通索引任务。
- 仅在全局开关与本任务开关均开启时执行实际扫描。
"""

from __future__ import annotations

import logging

import click
from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError

from configs import dify_config
from core.rag.graph_indexing.coordinator import GraphIndexJobCoordinator
from core.rag.graph_indexing.reconciler import GraphIndexReconciler
from core.rag.graph_indexing.repositories import SqlAlchemyGraphIndexJobRepository
from extensions.ext_database import db

logger = logging.getLogger(__name__)


class _CeleryDispatcher:
    """把 Coordinator 的投递调用桥接到 Celery 任务。

    采用延迟 import 规避 ``schedule.graph_reconcile_task`` 与
    ``tasks.graph_indexing_task`` 之间的模块加载循环。
    """

    def __call__(self, job_id: str) -> None:
        from tasks.graph_indexing_task import graph_indexing_task

        graph_indexing_task.delay(job_id)


def _build_reconciler() -> GraphIndexReconciler:
    """构造一个使用当前请求 session 的 Reconciler。"""
    repository = SqlAlchemyGraphIndexJobRepository(db.session)
    coordinator = GraphIndexJobCoordinator(repository=repository, dispatcher=_CeleryDispatcher())
    return GraphIndexReconciler(repository=repository, coordinator=coordinator)


@shared_task(queue="graph_index")
def graph_reconcile_task() -> None:
    """GraphRAG 对账任务入口。"""
    if not dify_config.GRAPH_RAG_ENABLED or not dify_config.ENABLE_GRAPH_RECONCILE_TASK:
        logger.debug("graph_reconcile_task skipped: flags disabled")
        return
    click.echo(click.style("Start graph index reconcile.", fg="green"))
    try:
        with db.session.begin():
            reconciler = _build_reconciler()
            reconciler.reconcile()
    except SQLAlchemyError:
        logger.exception("graph_reconcile_task failed with database error")
        raise
    except Exception:
        logger.exception("graph_reconcile_task failed")
        raise
