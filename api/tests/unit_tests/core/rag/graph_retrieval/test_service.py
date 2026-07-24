"""Graph Retrieval Service 的当前版本、DB 回查与 fail-open 测试。"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.rag.graph.entities import GraphQuery, GraphQueryMode, GraphResult
from core.rag.graph_indexing.entities import GraphIndexJobStatus
from core.rag.graph_indexing.versioning import (
    build_segment_source_facts,
    build_source_facts,
    compute_source_version,
)
from core.rag.graph_retrieval.neo4j_reader import GraphReadError
from core.rag.graph_retrieval.service import GraphRetrievalError, retrieve_graph_documents
from libs.datetime_utils import naive_utc_now
from models.base import TypeBase
from models.dataset import ChildChunk, Document, DocumentSegment
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob
from models.enums import SegmentStatus


@pytest.fixture
def db_session(_unit_test_engine):
    from core.db.session_factory import create_session

    session = create_session()
    TypeBase.metadata.create_all(
        session.bind,
        tables=[
            DatasetGraphConfig.__table__,
            DatasetGraphIndexJob.__table__,
            Document.__table__,
            DocumentSegment.__table__,
            ChildChunk.__table__,
        ],
    )
    yield session
    session.rollback()
    session.close()


def _seed_graph_dataset(db_session, *, query_mode: GraphQueryMode = GraphQueryMode.HYBRID):
    tenant_id, dataset_id = str(uuid4()), str(uuid4())
    now = naive_utc_now()
    document = Document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        position=0,
        data_source_type="upload_file",
        batch="batch-1",
        name="GraphRAG guide",
        created_from="api",
        created_by=str(uuid4()),
        completed_at=now,
        indexing_status="completed",
        word_count=20,
        updated_at=now,
        enabled=True,
        archived=False,
    )
    db_session.add(document)
    db_session.flush()
    segment = DocumentSegment(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document.id,
        position=1,
        content="Dify contains a Knowledge module.",
        word_count=6,
        tokens=8,
        created_by=str(uuid4()),
        enabled=True,
        status=SegmentStatus.COMPLETED,
        index_node_id="index-node-1",
    )
    db_session.add(segment)
    db_session.flush()
    source_version = compute_source_version(
        build_source_facts(
            document.id,
            updated_at=document.updated_at,
            completed_at=document.completed_at,
            batch=document.batch,
            word_count=document.word_count,
        ),
        [
            build_segment_source_facts(
                segment.id,
                content=segment.content,
                updated_at=segment.updated_at,
            )
        ],
    )
    config = DatasetGraphConfig(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        enabled=True,
        query_mode=query_mode,
        graph_top_k=5,
        graph_timeout_ms=900,
        graph_weight=0.4,
        graph_version="v2",
        extract_model_config={"provider": "provider", "model": "model"},
    )
    job = DatasetGraphIndexJob(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document.id,
        source_version=source_version,
        graph_version="v2",
        status=GraphIndexJobStatus.SUCCEEDED,
        completed_at=now,
    )
    db_session.add_all([config, job])
    db_session.flush()
    return tenant_id, dataset_id, document, segment, source_version


def test_service_queries_only_current_successful_versions_and_returns_valid_segment(db_session):
    from core.rag.graph_retrieval import service as module

    tenant_id, dataset_id, document, segment, source_version = _seed_graph_dataset(db_session)
    graph_result = GraphResult(
        segment_id=segment.id,
        graph_rank=1,
        graph_distance=1,
        graph_path=["Dify -[CONTAINS]-> Knowledge"],
        matched_entities=["Dify"],
        relation_types=["CONTAINS"],
    )
    graph_query = GraphQuery(entity_names=["Dify"], limit=5)

    with (
        patch.object(module.dify_config, "GRAPH_RAG_ENABLED", True),
        patch.object(module.dify_config, "GRAPH_RAG_FAIL_OPEN", True),
        patch.object(module, "analyze_graph_query", return_value=graph_query),
        patch.object(module, "read_graph_results", return_value=[graph_result]) as reader,
    ):
        batch = retrieve_graph_documents(
            session=db_session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            query="Dify 有哪些模块？",
        )

    assert batch.graph_weight == 0.4
    assert len(batch.documents) == 1
    result = batch.documents[0]
    assert result.page_content == segment.content
    assert result.metadata["doc_id"] == "index-node-1"
    assert result.metadata["segment_id"] == segment.id
    assert result.metadata["document_id"] == document.id
    assert result.metadata["retrieval_source"] == "graph"
    assert result.metadata["graph_path"] == ["Dify -[CONTAINS]-> Knowledge"]
    active_versions = reader.call_args.kwargs["active_versions"]
    assert [(item.document_id, item.source_version) for item in active_versions] == [(document.id, source_version)]


def test_service_excludes_disabled_segment_before_calling_neo4j(db_session):
    from core.rag.graph_retrieval import service as module

    tenant_id, dataset_id, _document, segment, _source_version = _seed_graph_dataset(db_session)
    segment.enabled = False
    db_session.flush()

    with (
        patch.object(module.dify_config, "GRAPH_RAG_ENABLED", True),
        patch.object(module, "analyze_graph_query") as analyzer,
        patch.object(module, "read_graph_results") as reader,
    ):
        batch = retrieve_graph_documents(
            session=db_session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            query="Dify",
        )

    assert batch.documents == []
    analyzer.assert_not_called()
    reader.assert_not_called()


def test_vector_mode_keeps_graph_reader_disabled(db_session):
    from core.rag.graph_retrieval import service as module

    tenant_id, dataset_id, *_ = _seed_graph_dataset(db_session, query_mode=GraphQueryMode.VECTOR)
    with (
        patch.object(module.dify_config, "GRAPH_RAG_ENABLED", True),
        patch.object(module, "read_graph_results") as reader,
    ):
        batch = retrieve_graph_documents(
            session=db_session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            query="Dify",
        )

    assert batch.documents == []
    reader.assert_not_called()


def test_reader_failure_fails_open_when_configured(db_session):
    from core.rag.graph_retrieval import service as module

    tenant_id, dataset_id, *_ = _seed_graph_dataset(db_session)
    with (
        patch.object(module.dify_config, "GRAPH_RAG_ENABLED", True),
        patch.object(module.dify_config, "GRAPH_RAG_FAIL_OPEN", True),
        patch.object(module, "analyze_graph_query", return_value=GraphQuery(entity_names=["Dify"])),
        patch.object(module, "read_graph_results", side_effect=GraphReadError("timeout")),
    ):
        batch = retrieve_graph_documents(
            session=db_session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            query="Dify",
        )

    assert batch.documents == []
    assert batch.degraded_reason == "graph_retrieval_failed"


def test_reader_failure_propagates_when_fail_open_disabled(db_session):
    from core.rag.graph_retrieval import service as module

    tenant_id, dataset_id, *_ = _seed_graph_dataset(db_session)
    with (
        patch.object(module.dify_config, "GRAPH_RAG_ENABLED", True),
        patch.object(module.dify_config, "GRAPH_RAG_FAIL_OPEN", False),
        patch.object(module, "analyze_graph_query", return_value=GraphQuery(entity_names=["Dify"])),
        patch.object(module, "read_graph_results", side_effect=GraphReadError("timeout")),
    ):
        with pytest.raises(GraphRetrievalError):
            retrieve_graph_documents(
                session=db_session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                query="Dify",
            )


def test_service_retrieves_existing_graph_when_extraction_is_disabled(db_session):
    """图检索不能依赖图谱抽取开关或抽取模型。"""
    from core.rag.graph_retrieval import service as module

    tenant_id, dataset_id, _document, segment, _source_version = _seed_graph_dataset(db_session)
    config = db_session.scalar(
        select(DatasetGraphConfig).where(DatasetGraphConfig.dataset_id == dataset_id)
    )
    assert config is not None
    config.index_enabled = False
    config.extract_model_config = None
    db_session.flush()

    graph_query = GraphQuery(entity_names=["Dify"], limit=5)
    graph_result = GraphResult(segment_id=segment.id, graph_rank=1, graph_distance=1)
    with (
        patch.object(module.dify_config, "GRAPH_RAG_ENABLED", True),
        patch.object(module, "analyze_graph_query", return_value=graph_query) as analyzer,
        patch.object(module, "read_graph_results", return_value=[graph_result]),
    ):
        batch = retrieve_graph_documents(
            session=db_session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            query="Dify",
        )

    assert [document.page_content for document in batch.documents] == [segment.content]
    assert analyzer.call_args.kwargs["model_config"] is None
