"""GraphRAG 对账定时任务。

该任务由 Celery Beat 按 ``GRAPH_RECONCILE_INTERVAL_MINUTES`` 触发。数据库
事务只负责创建、恢复和查询 Job；Celery 派发在事务提交后执行，避免 Worker
先于 Job 可见。
"""

from __future__ import annotations

import logging

import click
from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from configs import dify_config
from core.rag.graph_indexing.coordinator import GraphIndexJobCoordinator
from core.rag.graph_indexing.neo4j_writer import reconcile_dataset_graph
from core.rag.graph_indexing.reconciler import GraphIndexReconciler, GraphReconcilePlan
from core.rag.graph_indexing.repositories import SqlAlchemyGraphIndexJobRepository
from extensions.ext_database import db

logger = logging.getLogger(__name__)


class _CeleryDispatcher:
    """把 Job ID 投递到独立 graph_index 队列。"""

    def __call__(self, job_id: str) -> None:
        from tasks.graph_indexing_task import graph_indexing_task

        graph_indexing_task.delay(job_id)


def _reconcile_jobs() -> GraphReconcilePlan:
    """在短事务中执行一轮对账，返回提交后需要执行的动作。"""
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        repository = SqlAlchemyGraphIndexJobRepository(session)
        coordinator = GraphIndexJobCoordinator(repository=repository)
        reconciler = GraphIndexReconciler(repository=repository, coordinator=coordinator)
        return reconciler.reconcile_plan()


def _reconcile_graph_data(plan: GraphReconcilePlan) -> None:
    """在数据库事务之外清理失效图数据；失败由下一轮继续补偿。"""
    for command in plan.cleanup_commands:
        try:
            reconcile_dataset_graph(
                tenant_id=command.tenant_id,
                dataset_id=command.dataset_id,
                active_document_ids=command.active_document_ids,
                active_segment_ids=command.active_segment_ids,
                index_node_id=command.index_node_id,
            )
        except Exception:
            logger.exception(
                "failed to reconcile graph lifecycle tenant=%s dataset=%s scope=%s",
                command.tenant_id,
                command.dataset_id,
                command.index_node_id,
            )


def _dispatch_jobs(job_ids: tuple[str, ...] | list[str]) -> None:
    """在数据库事务之外派发 Job；失败由下一轮 Reconciler 补偿。"""
    dispatcher = _CeleryDispatcher()
    for job_id in job_ids:
        try:
            dispatcher(job_id)
        except Exception:
            logger.exception("failed to dispatch graph_index job=%s", job_id)


@shared_task(queue="graph_index")
def graph_reconcile_task() -> None:
    """GraphRAG 对账任务入口。"""
    if not dify_config.GRAPH_RAG_ENABLED or not dify_config.ENABLE_GRAPH_RECONCILE_TASK:
        logger.debug("graph_reconcile_task skipped: flags disabled")
        return
    click.echo(click.style("Start graph index reconcile.", fg="green"))
    try:
        plan = _reconcile_jobs()
    except SQLAlchemyError:
        logger.exception("graph_reconcile_task failed with database error")
        raise
    except Exception:
        logger.exception("graph_reconcile_task failed")
        raise

    _reconcile_graph_data(plan)
    _dispatch_jobs(plan.job_ids)


__all__ = ["graph_reconcile_task"]
