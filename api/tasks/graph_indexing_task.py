"""GraphRAG 索引 Worker 任务。

Phase 2 边界
=============

- 只实现 Job 的原子领取与状态迁移。
- 不实现实体关系抽取与 Neo4j 写入（Phase 3 适配器）。
- 没有 Adapter 时，Worker 必须显式标记为 retry_waiting，错误码
  `graph_indexer_not_implemented`，禁止伪成功。
- Worker 失败不得影响普通 Dataset 索引任务：使用独立 `graph_index` 队列，
  且异常在本任务内消化，不向上抛出导致重试风暴。
"""

from __future__ import annotations

import logging

import click
from celery import shared_task
from sqlalchemy.exc import SQLAlchemyError

from configs import dify_config
from core.rag.graph_indexing.errors import (
    GraphIndexerNotImplementedError,
    GraphIndexJobClaimError,
    GraphIndexJobNotFoundError,
)
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
    """执行 Job 的实际处理。Phase 2 只标记未实现。"""
    # Phase 3 将在此注入 Graph 抽取适配器；当前阶段适配器缺失，必须显式失败。
    repository.mark_retry_waiting(
        job.id,
        error_code=GraphIndexerNotImplementedError.code,
        error_message="graph indexer adapter not implemented (phase 3)",
        retry_base_seconds=dify_config.GRAPH_INDEX_RETRY_BASE_SECONDS,
    )
    logger.info("graph_index job marked retry_waiting job=%s", job.id)
