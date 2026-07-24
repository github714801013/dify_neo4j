"""GraphRAG 索引适配器（Phase 3）。

职责
====

- 把单个 Graph Index Job 编排为：短事务加载配置与 segments → LLM 抽取 →
  Neo4j 写入 → 返回处理结果。
- 外部 LLM/Neo4j I/O 不持有数据库事务；Job 状态由 Worker 在独立短事务中
  持久化。
- 配置缺失属于不可恢复失败；LLM、解析和临时 Neo4j 异常进入重试。
- 写入前后核对 Document source_version；过期 Job 精确清理自身图版本并取消，
  防止旧版本晚完成后反向覆盖新图。

边界
====

- 不负责扫描 Document、领取 Job 或提交 Job 状态。
- 不修改普通索引任务与既有检索行为。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session

from core.rag.graph.entities import GraphExtractModelConfig, GraphSchema
from core.rag.graph_indexing.entities import DATASET_GRAPH_INDEX_SCOPE, GraphIndexJobStatus
from core.rag.graph_indexing.extractor import GraphExtractionError, extract_with_llm
from core.rag.graph_indexing.neo4j_writer import (
    GraphWriteError,
    discard_document_version,
    ensure_graph_schema,
    finalize_document_version,
    write_segment,
)
from core.rag.graph_indexing.scopes import (
    list_published_node_graph_scopes,
    resolve_published_node_graph_scope,
)
from core.rag.graph_indexing.versioning import (
    build_segment_source_facts,
    build_source_facts,
    compute_source_version,
)
from extensions.ext_database import db
from models.dataset import Document, DocumentSegment
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob
from models.enums import SegmentStatus

logger = logging.getLogger(__name__)

_MAX_SEGMENT_CHARS = 8000
_NEO4J_UNCONFIGURED_MARKER = "neo4j connection config is missing"


@dataclass(frozen=True)
class GraphIndexJobRequest:
    """Worker 提交领取事务后保留的不可变 Job 快照。"""

    id: str
    tenant_id: str
    dataset_id: str
    document_id: str
    graph_version: str
    source_version: str
    index_node_id: str = DATASET_GRAPH_INDEX_SCOPE

    @classmethod
    def from_job(cls, job: DatasetGraphIndexJob) -> GraphIndexJobRequest:
        return cls(
            id=job.id,
            tenant_id=job.tenant_id,
            dataset_id=job.dataset_id,
            document_id=job.document_id,
            graph_version=job.graph_version,
            source_version=job.source_version,
            index_node_id=job.index_node_id,
        )


@dataclass(frozen=True)
class GraphIndexOutcome:
    """Indexer 返回给 Worker 的终态或可重试结果。"""

    status: GraphIndexJobStatus
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def succeeded(cls) -> GraphIndexOutcome:
        return cls(status=GraphIndexJobStatus.SUCCEEDED)

    @classmethod
    def retry(cls, *, error_code: str, error_message: str) -> GraphIndexOutcome:
        return cls(
            status=GraphIndexJobStatus.RETRY_WAITING,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def failed(cls, *, error_code: str, error_message: str) -> GraphIndexOutcome:
        return cls(
            status=GraphIndexJobStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

    @classmethod
    def cancelled(cls, *, error_code: str, error_message: str) -> GraphIndexOutcome:
        return cls(
            status=GraphIndexJobStatus.CANCELLED,
            error_code=error_code,
            error_message=error_message,
        )


@dataclass(frozen=True)
class _SegmentInput:
    id: str
    content: str


@dataclass(frozen=True)
class _IndexInput:
    schema: GraphSchema
    extract_model: GraphExtractModelConfig
    segments: tuple[_SegmentInput, ...]


def run_indexer(job: GraphIndexJobRequest) -> GraphIndexOutcome:
    """处理单个 Graph Index Job，并返回由 Worker 持久化的结果。"""
    if job.index_node_id != DATASET_GRAPH_INDEX_SCOPE and not _is_node_scope_current(job):
        return _cancel_missing_node_scope(job)
    if not _is_source_version_current(job):
        return _discard_stale_version(job)

    index_input = _load_index_input(job)
    if index_input is None:
        if job.index_node_id != DATASET_GRAPH_INDEX_SCOPE:
            return _cancel_missing_node_scope(job)
        return GraphIndexOutcome.failed(
            error_code="graph_indexer_config_missing",
            error_message="dataset graph config missing or disabled",
        )

    total_relations = 0
    processed_segments = 0
    try:
        ensure_graph_schema()
        for segment in index_input.segments:
            text = segment.content[:_MAX_SEGMENT_CHARS]
            if not text.strip():
                continue
            extraction = extract_with_llm(
                tenant_id=job.tenant_id,
                provider=index_input.extract_model.provider,
                model=index_input.extract_model.model,
                temperature=index_input.extract_model.temperature,
                max_tokens=index_input.extract_model.max_tokens,
                segment_text=text,
                schema=index_input.schema,
                strict=index_input.extract_model.strict,
                max_triplets_per_chunk=index_input.extract_model.max_triplets_per_chunk,
            )
            total_relations += write_segment(
                tenant_id=job.tenant_id,
                dataset_id=job.dataset_id,
                document_id=job.document_id,
                segment_id=segment.id,
                graph_version=job.graph_version,
                source_version=job.source_version,
                index_node_id=job.index_node_id,
                extraction=extraction,
            )
            processed_segments += 1

        if job.index_node_id != DATASET_GRAPH_INDEX_SCOPE and not _is_node_scope_current(job):
            return _cancel_missing_node_scope(job)
        if not _is_source_version_current(job):
            return _discard_stale_version(job)

        finalize_document_version(
            tenant_id=job.tenant_id,
            dataset_id=job.dataset_id,
            document_id=job.document_id,
            graph_version=job.graph_version,
            source_version=job.source_version,
            index_node_id=job.index_node_id,
        )
        if job.index_node_id != DATASET_GRAPH_INDEX_SCOPE and not _is_node_scope_current(job):
            return _cancel_missing_node_scope(job)
        if not _is_source_version_current(job):
            return _discard_stale_version(job)
    except GraphExtractionError as ex:
        message = ex.args[0] if ex.args else "extract failed"
        logger.warning("graph_index extract failed job=%s err=%s", job.id, ex)
        return GraphIndexOutcome.retry(
            error_code="graph_indexer_extract_failed",
            error_message=message,
        )
    except GraphWriteError as ex:
        message = ex.args[0] if ex.args else "write failed"
        logger.warning("graph_index write failed job=%s err=%s", job.id, ex)
        if _NEO4J_UNCONFIGURED_MARKER in message:
            return GraphIndexOutcome.failed(
                error_code="graph_indexer_neo4j_unconfigured",
                error_message=message,
            )
        return GraphIndexOutcome.retry(
            error_code="graph_indexer_write_failed",
            error_message=message,
        )

    logger.info(
        "graph_index job processed job=%s segments=%s relations=%s",
        job.id,
        processed_segments,
        total_relations,
    )
    return GraphIndexOutcome.succeeded()


def _discard_stale_version(job: GraphIndexJobRequest) -> GraphIndexOutcome:
    """清理过期 Job 的精确图版本，并返回 cancelled 或可重试结果。"""
    try:
        discard_document_version(
            tenant_id=job.tenant_id,
            dataset_id=job.dataset_id,
            document_id=job.document_id,
            graph_version=job.graph_version,
            source_version=job.source_version,
            index_node_id=job.index_node_id,
        )
    except GraphWriteError as ex:
        message = ex.args[0] if ex.args else "discard stale graph version failed"
        logger.warning("graph_index stale version discard failed job=%s err=%s", job.id, ex)
        if _NEO4J_UNCONFIGURED_MARKER in message:
            return GraphIndexOutcome.failed(
                error_code="graph_indexer_neo4j_unconfigured",
                error_message=message,
            )
        return GraphIndexOutcome.retry(
            error_code="graph_indexer_write_failed",
            error_message=message,
        )

    logger.info(
        "graph_index job cancelled because source version is stale job=%s source_version=%s",
        job.id,
        job.source_version,
    )
    return GraphIndexOutcome.cancelled(
        error_code="graph_indexer_source_stale",
        error_message="document source version changed while graph indexing",
    )


def _cancel_missing_node_scope(job: GraphIndexJobRequest) -> GraphIndexOutcome:
    """节点配置被删除、禁用或换版时取消 Job，保留该 scope 的既有图数据。"""
    return GraphIndexOutcome.cancelled(
        error_code="graph_indexer_node_scope_inactive",
        error_message="published node graph config is missing, disabled, or changed",
    )


def _is_node_scope_current(job: GraphIndexJobRequest) -> bool:
    with Session(db.engine, expire_on_commit=False) as session:
        scope = resolve_published_node_graph_scope(
            session,
            tenant_id=job.tenant_id,
            dataset_id=job.dataset_id,
            index_node_id=job.index_node_id,
        )
    return scope is not None and scope.graph_version == job.graph_version


def _is_source_version_current(job: GraphIndexJobRequest) -> bool:
    """核对 Job source_version 是否仍对应当前有效 Document 与 scope Segment。"""
    with Session(db.engine, expire_on_commit=False) as session:
        document = session.scalar(
            select(Document).where(
                Document.id == job.document_id,
                Document.tenant_id == job.tenant_id,
                Document.dataset_id == job.dataset_id,
            )
        )
        if (
            document is None
            or not document.enabled
            or document.archived
            or document.indexing_status != "completed"
            or document.completed_at is None
        ):
            return False
        segment_scope = _segment_scope_predicate(session, job)
        segments = session.scalars(
            select(DocumentSegment)
            .where(
                DocumentSegment.tenant_id == job.tenant_id,
                DocumentSegment.dataset_id == job.dataset_id,
                DocumentSegment.document_id == job.document_id,
                DocumentSegment.enabled.is_(True),
                DocumentSegment.status == SegmentStatus.COMPLETED,
                segment_scope,
            )
            .order_by(DocumentSegment.position, DocumentSegment.id)
        ).all()
        facts = build_source_facts(
            document.id,
            updated_at=document.updated_at,
            completed_at=document.completed_at,
            batch=document.batch,
            word_count=document.word_count,
        )
        segment_facts = [
            build_segment_source_facts(
                segment.id,
                content=segment.content or "",
                updated_at=segment.updated_at,
            )
            for segment in segments
        ]
        return compute_source_version(facts, segment_facts) == job.source_version


def _load_index_input(job: GraphIndexJobRequest) -> _IndexInput | None:
    """在短数据库会话中加载当前 scope 配置和 Segment 快照。"""
    with Session(db.engine, expire_on_commit=False) as session:
        if job.index_node_id == DATASET_GRAPH_INDEX_SCOPE:
            config = session.scalar(
                select(DatasetGraphConfig).where(
                    DatasetGraphConfig.tenant_id == job.tenant_id,
                    DatasetGraphConfig.dataset_id == job.dataset_id,
                )
            )
            if config is None or not config.enabled or config.extract_model_config is None:
                return None
            schema = GraphSchema.model_validate(config.schema_json)
            extract_model = GraphExtractModelConfig.model_validate(config.extract_model_config)
        else:
            node_scope = resolve_published_node_graph_scope(
                session,
                tenant_id=job.tenant_id,
                dataset_id=job.dataset_id,
                index_node_id=job.index_node_id,
            )
            if node_scope is None or node_scope.graph_version != job.graph_version:
                return None
            graph_config = node_scope.graph_config
            if graph_config.schema is None or graph_config.extract_model_config is None:
                return None
            schema = GraphSchema(
                entity_types=[item.name for item in graph_config.schema.entity_types],
                relation_types=[item.name for item in graph_config.schema.relation_types],
                allowed_triples=[item.as_tuple() for item in graph_config.schema.allowed_triples],
                entity_properties={item.name: item.properties for item in graph_config.schema.entity_types},
            )
            extract_model = graph_config.extract_model_config

        segment_scope = _segment_scope_predicate(session, job)
        segments = session.scalars(
            select(DocumentSegment)
            .where(
                DocumentSegment.document_id == job.document_id,
                DocumentSegment.tenant_id == job.tenant_id,
                DocumentSegment.dataset_id == job.dataset_id,
                DocumentSegment.enabled.is_(True),
                DocumentSegment.status == SegmentStatus.COMPLETED,
                segment_scope,
            )
            .order_by(DocumentSegment.position.asc())
        ).all()
        segment_inputs = tuple(_SegmentInput(id=segment.id, content=segment.content or "") for segment in segments)
        return _IndexInput(schema=schema, extract_model=extract_model, segments=segment_inputs)


def _segment_scope_predicate(session: Session, job: GraphIndexJobRequest):
    if job.index_node_id != DATASET_GRAPH_INDEX_SCOPE:
        return DocumentSegment.index_node_id == job.index_node_id
    excluded_index_node_ids = {
        scope.index_node_id
        for scope in list_published_node_graph_scopes(
            session,
            tenant_id=job.tenant_id,
            dataset_id=job.dataset_id,
        )
    }
    if not excluded_index_node_ids:
        return true()
    return or_(
        DocumentSegment.index_node_id.is_(None),
        DocumentSegment.index_node_id.not_in(excluded_index_node_ids),
    )


__all__ = [
    "GraphIndexJobRequest",
    "GraphIndexOutcome",
    "run_indexer",
]
