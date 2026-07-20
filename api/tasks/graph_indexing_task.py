"""GraphRAG 索引 Worker 任务。

事务边界
========

1. 使用短事务原子领取 Job，并提交 ``running`` 状态。
2. 在数据库事务外执行 LLM 抽取与 Neo4j 写入。
3. 使用新的短事务持久化 succeeded/retry_waiting/failed/cancelled 结果。

Worker 失败不得影响普通 Dataset 索引任务：使用独立 ``graph_index`` 队列，
并在任务内记录错误，避免 Celery 自动重试造成风暴。
"""

from __future__ import annotations

import logging

import click
from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from configs import dify_config
from core.rag.graph_indexing.entities import GraphIndexJobStatus
from core.rag.graph_indexing.errors import (
    GraphIndexJobClaimError,
    GraphIndexJobNotFoundError,
)
from core.rag.graph_indexing.indexer import (
    GraphIndexJobRequest,
    GraphIndexOutcome,
    run_indexer,
)
from core.rag.graph_indexing.repositories import SqlAlchemyGraphIndexJobRepository
from extensions.ext_database import db

logger = logging.getLogger(__name__)


@shared_task(queue="graph_index")
def graph_indexing_task(job_id: str) -> None:
    """领取并处理单个 Graph Index Job。"""
    click.echo(click.style(f"Start graph index job: {job_id}", fg="green"))
    try:
        job = _claim_job(job_id)
    except (GraphIndexJobNotFoundError, GraphIndexJobClaimError) as ex:
        logger.warning("graph_index job not claimable job=%s reason=%s", job_id, ex.code)
        return
    except SQLAlchemyError:
        logger.exception("graph_index job claim failed with database error job=%s", job_id)
        return

    try:
        outcome = run_indexer(job)
    except Exception:
        logger.exception("graph_indexer raised unexpected error job=%s", job_id)
        outcome = GraphIndexOutcome.retry(
            error_code="graph_indexer_unexpected_error",
            error_message="indexer raised unexpected exception",
        )

    try:
        _persist_outcome(job_id, outcome)
    except (GraphIndexJobNotFoundError, GraphIndexJobClaimError):
        logger.exception("graph_index outcome cannot be persisted job=%s status=%s", job_id, outcome.status)
    except SQLAlchemyError:
        logger.exception("graph_index outcome persistence failed with database error job=%s", job_id)


def _claim_job(job_id: str) -> GraphIndexJobRequest:
    """在独立短事务中原子领取 Job，并返回提交后可安全使用的快照。"""
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        repository = SqlAlchemyGraphIndexJobRepository(session)
        job = repository.claim(job_id)
        return GraphIndexJobRequest.from_job(job)


def _persist_outcome(job_id: str, outcome: GraphIndexOutcome) -> None:
    """在独立短事务中持久化 Indexer 结果。"""
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        repository = SqlAlchemyGraphIndexJobRepository(session)
        if outcome.status == GraphIndexJobStatus.SUCCEEDED:
            repository.mark_succeeded(job_id)
            return

        error_code = outcome.error_code or "graph_indexer_unknown_error"
        error_message = outcome.error_message or "graph indexer returned no error message"
        if outcome.status == GraphIndexJobStatus.CANCELLED:
            repository.mark_cancelled(
                job_id,
                error_code=error_code,
                error_message=error_message,
            )
            return
        if outcome.status == GraphIndexJobStatus.FAILED:
            repository.mark_failed(
                job_id,
                error_code=error_code,
                error_message=error_message,
            )
            return
        if outcome.status == GraphIndexJobStatus.RETRY_WAITING:
            repository.mark_retry_waiting(
                job_id,
                error_code=error_code,
                error_message=error_message,
                retry_base_seconds=dify_config.GRAPH_INDEX_RETRY_BASE_SECONDS,
                max_retries=dify_config.GRAPH_INDEX_MAX_RETRIES,
            )
            return
        raise ValueError(f"unsupported graph index outcome: {outcome.status}")


__all__ = ["graph_indexing_task"]
