"""Graph Retrieval 业务服务。

职责
====

- 读取 Dataset GraphRAG 配置并确认全局/Dataset 开关与 ``hybrid`` 模式。
- 只把与当前 Document + 有效 Segment 内容哈希一致的 succeeded Job 版本交给
  Neo4j Reader。
- 将图命中回查为当前启用、已完成、未归档且属于同一 Tenant/Dataset 的 Dify
  Segment，再映射为现有 ``Document`` 候选。
- Neo4j、模型或解析异常按 ``GRAPH_RAG_FAIL_OPEN`` 降级为空图候选。

边界
====

- 不修改基础向量/关键词检索。
- 不执行候选融合或 Reranker；由 ``fusion.py`` 和 Dataset Retrieval Hook 负责。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from configs import dify_config
from core.rag.graph.entities import GraphExtractModelConfig, GraphQueryMode, GraphSchema
from core.rag.graph_indexing.entities import GraphIndexJobStatus
from core.rag.graph_indexing.versioning import (
    build_segment_source_facts,
    build_source_facts,
    compute_source_version,
)
from core.rag.graph_retrieval.neo4j_reader import ActiveGraphVersion, read_graph_results
from core.rag.graph_retrieval.query_analyzer import analyze_graph_query
from core.rag.index_processor.constant.index_type import IndexStructureType
from core.rag.models.document import Document
from models.dataset import ChildChunk, DocumentSegment
from models.dataset import Document as DatasetDocument
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob
from models.enums import SegmentStatus

logger = logging.getLogger(__name__)


class GraphRetrievalError(Exception):
    """Graph Retrieval 未降级时向上层传播的领域错误。"""


@dataclass(frozen=True)
class GraphRetrievalBatch:
    """单个 Dataset 的图候选及融合配置。"""

    documents: list[Document]
    graph_weight: float
    degraded_reason: str | None = None


def retrieve_graph_documents(
    *,
    session: Session,
    tenant_id: str,
    dataset_id: str,
    query: str,
    document_ids_filter: list[str] | None = None,
) -> GraphRetrievalBatch:
    """查询单个 Dataset 的当前有效图候选。"""
    if not bool(getattr(dify_config, "GRAPH_RAG_ENABLED", False)) or not query.strip():
        return GraphRetrievalBatch(documents=[], graph_weight=0)

    config = session.scalar(
        select(DatasetGraphConfig).where(
            DatasetGraphConfig.tenant_id == tenant_id,
            DatasetGraphConfig.dataset_id == dataset_id,
        )
    )
    if config is None or not config.enabled or config.query_mode != GraphQueryMode.HYBRID:
        return GraphRetrievalBatch(documents=[], graph_weight=0)

    graph_weight = float(config.graph_weight)
    started_at = time.perf_counter()
    try:
        active_versions = _load_active_versions(
            session=session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            graph_version=config.graph_version,
            document_ids_filter=document_ids_filter,
        )
        if not active_versions:
            return GraphRetrievalBatch(documents=[], graph_weight=graph_weight)

        schema = GraphSchema.model_validate(config.schema_json)
        model_config = (
            GraphExtractModelConfig.model_validate(config.extract_model_config)
            if config.extract_model_config is not None
            else None
        )
        graph_query = analyze_graph_query(
            tenant_id=tenant_id,
            query=query,
            model_config=model_config,
            schema=schema,
            limit=config.graph_top_k,
        )
        if graph_query is None:
            return GraphRetrievalBatch(documents=[], graph_weight=graph_weight)

        graph_results = read_graph_results(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            graph_version=config.graph_version,
            active_versions=active_versions,
            graph_query=graph_query,
            timeout_ms=config.graph_timeout_ms,
        )
        documents = _load_valid_graph_documents(
            session=session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            graph_results=graph_results,
            document_ids_filter=document_ids_filter,
        )
        logger.info(
            "graph_retrieval completed tenant=%s dataset=%s elapsed_ms=%s candidates=%s",
            tenant_id,
            dataset_id,
            int((time.perf_counter() - started_at) * 1000),
            len(documents),
        )
        return GraphRetrievalBatch(documents=documents, graph_weight=graph_weight)
    except Exception as ex:
        if bool(getattr(dify_config, "GRAPH_RAG_FAIL_OPEN", True)):
            logger.warning(
                "graph_retrieval degraded tenant=%s dataset=%s elapsed_ms=%s reason=%s",
                tenant_id,
                dataset_id,
                int((time.perf_counter() - started_at) * 1000),
                type(ex).__name__,
            )
            return GraphRetrievalBatch(
                documents=[],
                graph_weight=graph_weight,
                degraded_reason="graph_retrieval_failed",
            )
        raise GraphRetrievalError(f"graph retrieval failed for dataset {dataset_id}: {ex}") from ex


def _load_active_versions(
    *,
    session: Session,
    tenant_id: str,
    dataset_id: str,
    graph_version: str,
    document_ids_filter: list[str] | None,
) -> tuple[ActiveGraphVersion, ...]:
    jobs_stmt = (
        select(DatasetGraphIndexJob)
        .where(
            DatasetGraphIndexJob.tenant_id == tenant_id,
            DatasetGraphIndexJob.dataset_id == dataset_id,
            DatasetGraphIndexJob.graph_version == graph_version,
            DatasetGraphIndexJob.status == GraphIndexJobStatus.SUCCEEDED,
        )
        .order_by(DatasetGraphIndexJob.completed_at.desc(), DatasetGraphIndexJob.created_at.desc())
    )
    if document_ids_filter:
        jobs_stmt = jobs_stmt.where(DatasetGraphIndexJob.document_id.in_(document_ids_filter))
    jobs = session.scalars(jobs_stmt).all()
    if not jobs:
        return ()

    jobs_by_document: dict[str, list[DatasetGraphIndexJob]] = defaultdict(list)
    for job in jobs:
        jobs_by_document[job.document_id].append(job)

    document_ids = list(jobs_by_document)
    documents = session.scalars(
        select(DatasetDocument).where(
            DatasetDocument.id.in_(document_ids),
            DatasetDocument.tenant_id == tenant_id,
            DatasetDocument.dataset_id == dataset_id,
            DatasetDocument.enabled.is_(True),
            DatasetDocument.archived.is_(False),
            DatasetDocument.indexing_status == "completed",
            DatasetDocument.completed_at.is_not(None),
        )
    ).all()
    if not documents:
        return ()

    segments = session.scalars(
        select(DocumentSegment)
        .where(
            DocumentSegment.tenant_id == tenant_id,
            DocumentSegment.dataset_id == dataset_id,
            DocumentSegment.document_id.in_([document.id for document in documents]),
            DocumentSegment.enabled.is_(True),
            DocumentSegment.status == SegmentStatus.COMPLETED,
        )
        .order_by(DocumentSegment.document_id, DocumentSegment.position, DocumentSegment.id)
    ).all()
    segments_by_document: dict[str, list[DocumentSegment]] = defaultdict(list)
    for segment in segments:
        segments_by_document[segment.document_id].append(segment)

    active_versions: list[ActiveGraphVersion] = []
    for document in documents:
        source_version = _compute_current_source_version(document, segments_by_document.get(document.id, []))
        matching_job = next(
            (job for job in jobs_by_document[document.id] if job.source_version == source_version),
            None,
        )
        if matching_job is not None:
            active_versions.append(
                ActiveGraphVersion(document_id=document.id, source_version=matching_job.source_version)
            )
    return tuple(sorted(active_versions, key=lambda item: item.document_id))


def _compute_current_source_version(
    document: DatasetDocument,
    segments: list[DocumentSegment],
) -> str:
    document_facts = build_source_facts(
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
    return compute_source_version(document_facts, segment_facts)


def _load_valid_graph_documents(
    *,
    session: Session,
    tenant_id: str,
    dataset_id: str,
    graph_results,
    document_ids_filter: list[str] | None,
) -> list[Document]:
    if not graph_results:
        return []
    segment_ids = [result.segment_id for result in graph_results]
    stmt = (
        select(DocumentSegment, DatasetDocument)
        .join(DatasetDocument, DatasetDocument.id == DocumentSegment.document_id)
        .where(
            DocumentSegment.id.in_(segment_ids),
            DocumentSegment.tenant_id == tenant_id,
            DocumentSegment.dataset_id == dataset_id,
            DocumentSegment.enabled.is_(True),
            DocumentSegment.status == SegmentStatus.COMPLETED,
            DatasetDocument.tenant_id == tenant_id,
            DatasetDocument.dataset_id == dataset_id,
            DatasetDocument.enabled.is_(True),
            DatasetDocument.archived.is_(False),
            DatasetDocument.indexing_status == "completed",
            DatasetDocument.completed_at.is_not(None),
        )
    )
    if document_ids_filter:
        stmt = stmt.where(DocumentSegment.document_id.in_(document_ids_filter))
    rows = session.execute(stmt).all()
    row_by_segment = {segment.id: (segment, document) for segment, document in rows}

    parent_segment_ids = [
        segment.id
        for segment, document in row_by_segment.values()
        if document.doc_form == IndexStructureType.PARENT_CHILD_INDEX
    ]
    child_index_node_by_segment: dict[str, str] = {}
    if parent_segment_ids:
        child_chunks = session.scalars(
            select(ChildChunk)
            .where(
                ChildChunk.tenant_id == tenant_id,
                ChildChunk.dataset_id == dataset_id,
                ChildChunk.segment_id.in_(parent_segment_ids),
                ChildChunk.index_node_id.is_not(None),
            )
            .order_by(ChildChunk.segment_id, ChildChunk.position)
        ).all()
        for child_chunk in child_chunks:
            if child_chunk.index_node_id:
                child_index_node_by_segment.setdefault(child_chunk.segment_id, child_chunk.index_node_id)

    documents: list[Document] = []
    for graph_result in graph_results:
        row = row_by_segment.get(graph_result.segment_id)
        if row is None:
            continue
        segment, dataset_document = row
        if dataset_document.doc_form == IndexStructureType.PARENT_CHILD_INDEX:
            doc_id = child_index_node_by_segment.get(segment.id)
        else:
            doc_id = segment.index_node_id
        if not doc_id:
            logger.warning(
                "graph_retrieval skipped segment without retrievable index node tenant=%s dataset=%s segment=%s",
                tenant_id,
                dataset_id,
                segment.id,
            )
            continue
        documents.append(
            Document(
                page_content=segment.content,
                provider="dify",
                metadata={
                    "doc_id": doc_id,
                    "segment_id": segment.id,
                    "document_id": segment.document_id,
                    "dataset_id": segment.dataset_id,
                    "score": 1.0 / graph_result.graph_rank,
                    "retrieval_source": "graph",
                    "graph_rank": graph_result.graph_rank,
                    "graph_distance": graph_result.graph_distance,
                    "graph_path": graph_result.graph_path,
                    "graph_matched_entities": graph_result.matched_entities,
                    "graph_relation_types": graph_result.relation_types,
                },
            )
        )
    return documents


__all__ = [
    "GraphRetrievalBatch",
    "GraphRetrievalError",
    "retrieve_graph_documents",
]
