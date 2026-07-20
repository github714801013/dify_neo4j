"""Disposable Neo4j integration test for GraphRAG write, read, and replacement.

The same verification can run through pytest or directly with Python. Both entry points require
an explicit disposable flag and never fall back to the normal Dify Neo4j configuration.

```
GRAPH_RAG_NEO4J_TEST_DISPOSABLE=true \
GRAPH_RAG_NEO4J_TEST_URI=bolt://127.0.0.1:17687 \
GRAPH_RAG_NEO4J_TEST_USERNAME=neo4j \
GRAPH_RAG_NEO4J_TEST_PASSWORD=<test-password> \
python api/tests/integration_tests/core/rag/graph_indexing/test_neo4j_writer_integration.py
```
"""

from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from neo4j import GraphDatabase

from core.rag.graph.entities import GraphQuery
from core.rag.graph_indexing.extractor import ExtractedTriple, ExtractionResult
from core.rag.graph_retrieval.neo4j_reader import ActiveGraphVersion

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

    tenant_id = str(uuid4())
    dataset_id = str(uuid4())
    document_id = str(uuid4())
    driver = GraphDatabase.driver(_URI, auth=(_USERNAME, _PASSWORD))
    driver.verify_connectivity()

    previous_driver = writer._driver
    previous_schema_initialized = writer._schema_initialized
    writer._driver = driver
    writer._schema_initialized = False

    try:
        with (
            patch.object(writer, "_database_name", return_value=_DATABASE),
            patch.object(reader, "get_graph_driver", return_value=driver),
            patch.object(reader, "get_graph_database_name", return_value=_DATABASE),
        ):
            writer.ensure_graph_schema()
            common = {
                "tenant_id": tenant_id,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "graph_version": "v1",
                "extraction": _extraction(),
            }

            writer.write_segment(segment_id="old-segment", source_version="old-source", **common)
            writer.finalize_document_version(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                graph_version="v1",
                source_version="old-source",
            )

            writer.write_segment(segment_id="new-segment-1", source_version="new-source", **common)
            writer.write_segment(segment_id="new-segment-2", source_version="new-source", **common)
            writer.write_segment(segment_id="new-segment-2", source_version="new-source", **common)

            graph_results_before_finalize = reader.read_graph_results(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                graph_version="v1",
                active_versions=(ActiveGraphVersion(document_id, "new-source"),),
                graph_query=GraphQuery(entity_names=["Dify"], relation_types=["CONTAINS"], limit=10),
                timeout_ms=2000,
            )
            assert {item.segment_id for item in graph_results_before_finalize} == {
                "new-segment-1",
                "new-segment-2",
            }

            writer.finalize_document_version(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                graph_version="v1",
                source_version="new-source",
            )

            with driver.session(database=_DATABASE) as session:
                result = session.run(
                    "MATCH (segment:GraphSegment)-[:EVIDENCE_FOR]->(fact:GraphFact) "
                    "WHERE fact.tenant_id = $tenant_id AND fact.dataset_id = $dataset_id "
                    "AND fact.document_id = $document_id AND fact.graph_version = $graph_version "
                    "AND fact.source_version = $source_version "
                    "RETURN count(DISTINCT fact) AS fact_count, "
                    "count(DISTINCT segment) AS evidence_segment_count",
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                    graph_version="v1",
                    source_version="new-source",
                ).single(strict=True)
                old_count = session.run(
                    "MATCH (node) "
                    "WHERE (node:GraphEntity OR node:GraphFact OR node:GraphSegment) "
                    "AND node.tenant_id = $tenant_id AND node.dataset_id = $dataset_id "
                    "AND node.document_id = $document_id AND node.source_version = $source_version "
                    "RETURN count(node) AS count",
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                    source_version="old-source",
                ).single(strict=True)["count"]

            assert result["fact_count"] == 1
            assert result["evidence_segment_count"] == 2
            assert old_count == 0

            writer.reconcile_dataset_graph(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                active_document_ids=(document_id,),
                active_segment_ids=("new-segment-2",),
            )
            graph_results_after_segment_delete = reader.read_graph_results(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                graph_version="v1",
                active_versions=(ActiveGraphVersion(document_id, "new-source"),),
                graph_query=GraphQuery(entity_names=["Dify"], limit=10),
                timeout_ms=2000,
            )
            assert [item.segment_id for item in graph_results_after_segment_delete] == ["new-segment-2"]

            writer.reconcile_dataset_graph(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                active_document_ids=(),
                active_segment_ids=(),
            )
            assert (
                reader.read_graph_results(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    graph_version="v1",
                    active_versions=(ActiveGraphVersion(document_id, "new-source"),),
                    graph_query=GraphQuery(entity_names=["Dify"], limit=10),
                    timeout_ms=2000,
                )
                == []
            )
    finally:
        try:
            with driver.session(database=_DATABASE) as session:
                session.run(
                    "MATCH (node) WHERE node.tenant_id = $tenant_id DETACH DELETE node",
                    tenant_id=tenant_id,
                ).consume()
        finally:
            writer._driver = previous_driver
            writer._schema_initialized = previous_schema_initialized
            driver.close()


def test_multiple_evidence_reader_version_and_lifecycle() -> None:
    _run_verification()


if __name__ == "__main__":
    _run_verification()
    print("DISPOSABLE_NEO4J_GRAPH_VERIFICATION_PASSED")
