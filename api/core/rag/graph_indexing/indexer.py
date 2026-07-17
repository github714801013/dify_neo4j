"""GraphRAG 索引适配器（Phase 3）。

职责
====

- 把单个 Graph Index Job 编排为：加载配置与 segments → LLM 抽取 → Neo4j 写入
  → 标记 Job 终态。
- 复用 Phase 2 的状态机（mark_succeeded/mark_retry_waiting/mark_failed），
  不引入新的 Job 状态。
- 配置缺失、Neo4j 不可达等不可恢复错误倾向 failed；LLM/解析/临时网络错误
  倾向 retry_waiting。

边界
====

- 不负责扫描 Document 与领取 Job（Reconciler/Worker 已完成）。
- 不修改普通索引任务与既有检索行为。
- 只在 ``_process_job`` 被调用一次，对现有代码的侵入降到最低。
"""

from __future__ import annotations

import logging
from typing import Protocol

from sqlalchemy import select

from configs import dify_config
from core.rag.graph.entities import GraphExtractModelConfig, GraphSchema
from core.rag.graph_indexing.extractor import GraphExtractionError, extract_with_llm
from core.rag.graph_indexing.neo4j_writer import GraphWriteError, write_segment
from extensions.ext_database import db
from models.dataset import DocumentSegment
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob

logger = logging.getLogger(__name__)

# 单个 segment 抽取文本的最大长度，避免超长段落撑爆 LLM 上下文。
_MAX_SEGMENT_CHARS = 8000

# Neo4j 连接配置缺失时的错误特征文本，用于区分"不可恢复"与"可重试"。
_NEO4J_UNCONFIGURED_MARKER = "neo4j connection config is missing"


class _JobRepository(Protocol):
    """仅声明 indexer 用到的 repository 方法，避免依赖具体实现。"""

    def mark_succeeded(self, job_id: str) -> DatasetGraphIndexJob: ...
    def mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> DatasetGraphIndexJob: ...
    def mark_retry_waiting(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
        retry_base_seconds: int,
    ) -> DatasetGraphIndexJob: ...


def run_indexer(repository: _JobRepository, job: DatasetGraphIndexJob) -> None:
    """处理单个 Graph Index Job：抽取实体关系并写入 Neo4j。

    失败模式：
    - 配置缺失 / Neo4j 未配置：mark_failed（不可恢复）。
    - LLM 调用或解析失败 / Neo4j 写入临时错误：mark_retry_waiting。
    - 成功：mark_succeeded。
    """
    config = _load_graph_config(job)
    if config is None or not config.enabled or config.extract_model_config is None:
        repository.mark_failed(
            job.id,
            error_code="graph_indexer_config_missing",
            error_message="dataset graph config missing or disabled",
        )
        return

    schema = GraphSchema.model_validate(config.schema_json)
    extract_model = GraphExtractModelConfig.model_validate(config.extract_model_config)

    segments = db.session.scalars(
        select(DocumentSegment)
        .where(
            DocumentSegment.document_id == job.document_id,
            DocumentSegment.tenant_id == job.tenant_id,
            DocumentSegment.enabled.is_(True),
        )
        .order_by(DocumentSegment.position.asc())
    ).all()

    total_relations = 0
    processed_segments = 0
    try:
        for segment in segments:
            text = segment.content[:_MAX_SEGMENT_CHARS] if segment.content else ""
            if not text.strip():
                continue
            extraction = extract_with_llm(
                tenant_id=job.tenant_id,
                provider=extract_model.provider,
                model=extract_model.model,
                temperature=extract_model.temperature,
                max_tokens=extract_model.max_tokens,
                segment_text=text,
                schema=schema,
            )
            total_relations += write_segment(
                tenant_id=job.tenant_id,
                dataset_id=job.dataset_id,
                document_id=job.document_id,
                segment_id=segment.id,
                graph_version=job.graph_version,
                extraction=extraction,
            )
            processed_segments += 1
    except GraphExtractionError as ex:
        repository.mark_retry_waiting(
            job.id,
            error_code="graph_indexer_extract_failed",
            error_message=ex.args[0] if ex.args else "extract failed",
            retry_base_seconds=dify_config.GRAPH_INDEX_RETRY_BASE_SECONDS,
        )
        logger.warning("graph_index extract failed job=%s err=%s", job.id, ex)
        return
    except GraphWriteError as ex:
        message = ex.args[0] if ex.args else "write failed"
        # Neo4j 连接配置缺失属不可恢复错误，直接失败；其余按重试处理。
        if _NEO4J_UNCONFIGURED_MARKER in message:
            repository.mark_failed(
                job.id,
                error_code="graph_indexer_neo4j_unconfigured",
                error_message=message,
            )
        else:
            repository.mark_retry_waiting(
                job.id,
                error_code="graph_indexer_write_failed",
                error_message=message,
                retry_base_seconds=dify_config.GRAPH_INDEX_RETRY_BASE_SECONDS,
            )
        logger.warning("graph_index write failed job=%s err=%s", job.id, ex)
        return

    repository.mark_succeeded(job.id)
    logger.info(
        "graph_index job succeeded job=%s segments=%s relations=%s",
        job.id,
        processed_segments,
        total_relations,
    )


def _load_graph_config(job: DatasetGraphIndexJob) -> DatasetGraphConfig | None:
    """按 tenant+dataset 加载 GraphConfig。缺失返回 None，由调用方标记失败。"""
    return db.session.scalar(
        select(DatasetGraphConfig).where(
            DatasetGraphConfig.tenant_id == job.tenant_id,
            DatasetGraphConfig.dataset_id == job.dataset_id,
        )
    )


__all__ = ["run_indexer"]
