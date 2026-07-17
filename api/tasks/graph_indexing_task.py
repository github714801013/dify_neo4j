"""GraphRAG 索引 Worker 任务。

Phase 2 边界
=============

- 只实现 Job 的原子领取与状态迁移。
- 不实现实体关系抽取与 Neo4j 写入（Phase 3 适配器）。
- 没有 Adapter 时，Worker 必须显式标记为 retry_waiting，错误码
  `graph_indexer_not_implemented`，禁止伪成功。
- Worker 失败不得影响普通 Dataset 索引任务：使用独立 `graph_index` 队列，
  且异常在本任务内消化，不向上抛出导致重试风暴。

Phase 3
=======

- ``_process_job`` 委托给 ``core.rag.graph_indexing.indexer.run_indexer`` 完成
  LLM 实体抽取与 Neo4j 写入，并复用既有状态机标记终态。
- 适配器自身的异常已在内部消化为状态迁移，因此这里不再兜底重复标记。
"""

from __future__ import annotations

import logging

import click
from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError

from configs import dify_config
from core.rag.graph_indexing.errors import (
    GraphIndexJobClaimError,
    GraphIndexJobNotFoundError,
)
from core.rag.graph_indexing.indexer import run_indexer
from core.rag.graph_indexing.repositories import SqlAlchemyGraphIndexJobRepository
from extensions.ext_database import db
from models.dataset_graph_index_job import DatasetGraphIndexJob

logger = logging.getLogger(__name__)


@shared_task(queue="graph_index")
def graph_indexing_task(job_id: str) -> None:
    """处理单个 Graph Index Job。"""
    click.echo(click.style(f"Start graph index job: {job_id}", fg="green"))
    try:
        with db.session.begin():
            repository = SqlAlchemyGraphIndexJobRepository(db.session)
            try:
                job = repository.claim(job_id)
            except (GraphIndexJobNotFoundError, GraphIndexJobClaimError) as ex:
                logger.warning("graph_index job not claimable job=%s reason=%s", job_id, ex.code)
                return

            _process_job(repository, job)
    except SQLAlchemyError:
        logger.exception("graph_indexing_task failed with database error job=%s", job_id)
        return
    except Exception:
        logger.exception("graph_indexing_task failed job=%s", job_id)
        return


def _process_job(repository, job: DatasetGraphIndexJob) -> None:
    """执行 Job 的实际处理：LLM 抽取 + Neo4j 写入。"""
    # Phase 3：委托适配器完成抽取与写入；适配器内部按状态机标记终态，
    # 失败不抛出，避免 Worker 触发 Celery 重试风暴。
    try:
        run_indexer(repository, job)
    except Exception:
        # 适配器未预期的异常兜底为 retry_waiting，保证不伪成功且可被对账恢复。
        logger.exception("graph_indexing_task unexpected error job=%s", job.id)
        repository.mark_retry_waiting(
            job.id,
            error_code="graph_indexer_unexpected_error",
            error_message="indexer raised unexpected exception",
            retry_base_seconds=dify_config.GRAPH_INDEX_RETRY_BASE_SECONDS,
        )