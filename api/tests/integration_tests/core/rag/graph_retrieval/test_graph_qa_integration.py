"""Disposable Neo4j + in-memory Dify DB integration for graph-only QA retrieval.

This verifies the production-shaped path from Neo4j evidence through Graph Retrieval Service and
DatasetRetrieval fusion. It never connects to the normal Dify SQL database or Neo4j configuration.
"""

from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from neo4j import GraphDatabase
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from configs import dify_config
from core.rag.graph.entities import GraphQueryMode
from core.rag.graph_indexing.entities import GraphIndexJobStatus
from core.rag.graph_indexing.extractor import ExtractedTriple, ExtractionResult
from core.rag.graph_indexing.versioning import (
    build_segment_source_facts,
    build_source_facts,
    compute_source_version,
)
from core.rag.retrieval.dataset_retrieval import DatasetRetrieval
from libs.datetime_utils import naive_utc_now
from models.base import TypeBase
from models.dataset import Document, DocumentSegment
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob
from models.enums import SegmentStatus

_DISPOSABLE = os.getenv("GRAPH_RAG_NEO4J_TEST_DISPOSABLE", "").lower() == "true"
_URI = os.getenv("GRAPH_RAG_NEO4J_TEST_URI", "")
_USERNAME = os.getenv("GRAPH_RAG_NEO4J_TEST_USERNAME", "neo4j")
_PASSWORD = os.getenv("GRAPH_RAG_NEO4J_TEST_PASSWORD", "")
_DATABASE = os.getenv("GRAPH_RAG_NEO4J_TEST_DATABASE") or None

pytestmark = pytest.mark.skipif(
    not (_DISPOSABLE and _URI and _PASSWORD),
    reason="requires an explicitly marked disposable Neo4j instance",
)


def _extraction() -> ExtractionResult:
    return ExtractionResult(
        entities=[("Dify", "PRODUCT"), ("Knowledge", "MODULE")],
        triples=[
            ExtractedTriple(
                source="Dify",
                source_type="PRODUCT",
                relation="CONTAINS",
                target="Knowledge",
                target_type="MODULE",
            )
        ],
    )


def _run_verification() -> None:
    if not (_DISPOSABLE and _URI and _PASSWORD):
        raise RuntimeError("disposable Neo4j test environment is not explicitly configured")

    from core.rag.graph_indexing import neo4j_writer as writer
    from core.rag.graph_retrieval import neo4j_reader as reader
    from core.rag.graph_retrieval import query_analyzer

    tenant_id = str(uuid4())
    dataset_id = str(uuid4())
    now = naive_utc_now()
    engine = create_engine("sqlite:///:memory:")
    TypeBase.metadata.create_all(
        engine,
        tables=[
            DatasetGraphConfig.__table__,
            DatasetGraphIndexJob.__table__,
            Document.__table__,
            DocumentSegment.__table__,
        ],
    )
    driver = GraphDatabase.driver(_URI, auth=(_USERNAME, _PASSWORD))
    driver.verify_connectivity()
    previous_driver = writer._driver
    previous_schema_initialized = writer._schema_initialized
    writer._driver = driver
    writer._schema_initialized = False

    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
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
            session.add(document)
            session.flush()
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
                index_node_id="graph-index-node-1",
            )
            session.add(segment)
            session.flush()
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
            session.add_all(
                [
                    DatasetGraphConfig(
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        enabled=True,
                        query_mode=GraphQueryMode.HYBRID,
                        graph_top_k=5,
                        graph_timeout_ms=2000,
                        graph_weight=0.7,
                        graph_version="v1",
                        extract_model_config={"provider": "provider", "model": "model"},
                    ),
                    DatasetGraphIndexJob(
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        document_id=document.id,
                        source_version=source_version,
                        graph_version="v1",
                        status=GraphIndexJobStatus.SUCCEEDED,
                        completed_at=now,
                    ),
                ]
            )

        with (
            patch.object(writer, "_database_name", return_value=_DATABASE),
            patch.object(reader, "get_graph_driver", return_value=driver),
            patch.object(reader, "get_graph_database_name", return_value=_DATABASE),
            patch.object(query_analyzer, "_invoke_query_model", side_effect=RuntimeError("model unavailable")),
            patch.object(dify_config, "GRAPH_RAG_ENABLED", True),
            patch.object(dify_config, "GRAPH_RAG_FAIL_OPEN", False),
        ):
            writer.ensure_graph_schema()
            writer.write_segment(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document.id,
                segment_id=segment.id,
                graph_version="v1",
                source_version=source_version,
                extraction=_extraction(),
            )
            writer.finalize_document_version(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document.id,
                graph_version="v1",
                source_version=source_version,
            )

            with Session(engine, expire_on_commit=False) as session:
                results = DatasetRetrieval().augment_with_graph(
                    session=session,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    query="Dify 有哪些模块？",
                    base_documents=[],
                    top_k=4,
                    score_threshold=0,
                    reranking_enable=False,
                    reranking_mode="reranking_model",
                    reranking_model=None,
                    weights=None,
                    document_ids_filter=None,
                )

            assert len(results) == 1
            assert results[0].page_content == "Dify contains a Knowledge module."
            assert results[0].metadata["doc_id"] == "graph-index-node-1"
            assert results[0].metadata["segment_id"] == segment.id
            assert results[0].metadata["retrieval_sources"] == ["graph"]
            assert results[0].metadata["graph_matched_entities"] == ["Dify"]
    finally:
        try:
            with driver.session(database=_DATABASE) as neo4j_session:
                neo4j_session.run(
                    "MATCH (node) WHERE node.tenant_id = $tenant_id DETACH DELETE node",
                    tenant_id=tenant_id,
                ).consume()
        finally:
            writer._driver = previous_driver
            writer._schema_initialized = previous_schema_initialized
            driver.close()
            engine.dispose()


def test_graph_only_candidate_reaches_dataset_retrieval() -> None:
    _run_verification()


if __name__ == "__main__":
    _run_verification()
    print("DISPOSABLE_NEO4J_QA_RETRIEVAL_VERIFICATION_PASSED")
