"""Neo4j GraphRAG 写入模型与版本切换测试。"""

from dataclasses import dataclass, field
from datetime import date
from unittest.mock import patch

from core.rag.graph_indexing.entities import DATASET_GRAPH_INDEX_SCOPE
from core.rag.graph_indexing.extractor import ExtractedEntity, ExtractedTriple, ExtractionResult
from core.rag.graph_indexing.neo4j_writer import (
    discard_document_version,
    ensure_graph_schema,
    finalize_document_version,
    reconcile_dataset_graph,
    write_segment,
)


@dataclass
class _Run:
    query: str
    parameters: dict[str, object]


@dataclass
class _FakeTransaction:
    runs: list[_Run] = field(default_factory=list)

    def run(self, query: str, **parameters: object) -> None:
        self.runs.append(_Run(query=query, parameters=parameters))


class _FakeSession:
    def __init__(self, transaction: _FakeTransaction, schema_queries: list[str]) -> None:
        self.transaction = transaction
        self.schema_queries = schema_queries

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute_write(self, callback, **kwargs):
        return callback(self.transaction, **kwargs)

    def run(self, query: str) -> None:
        self.schema_queries.append(query)


class _FakeDriver:
    def __init__(self) -> None:
        self.transaction = _FakeTransaction()
        self.schema_queries: list[str] = []
        self.databases: list[str | None] = []

    def session(self, *, database: str | None = None) -> _FakeSession:
        self.databases.append(database)
        return _FakeSession(self.transaction, self.schema_queries)


def _extraction() -> ExtractionResult:
    return ExtractionResult(
        entities=[
            ExtractedEntity(
                name="Dify",
                entity_type="PRODUCT",
                properties={"display_name": "Dify 平台", "release_date": date(2026, 7, 24)},
            ),
            ExtractedEntity(name="Knowledge", entity_type="MODULE"),
        ],
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


def _write(driver: _FakeDriver, segment_id: str) -> int:
    from core.rag.graph_indexing import neo4j_writer as module

    with patch.object(module, "_get_driver", return_value=driver):
        return write_segment(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="document-1",
            segment_id=segment_id,
            graph_version="v2",
            source_version="source-v3",
            extraction=_extraction(),
        )


def test_same_fact_from_multiple_segments_keeps_multiple_evidence_edges():
    driver = _FakeDriver()

    assert _write(driver, "segment-1") == 1
    assert _write(driver, "segment-2") == 1

    fact_runs = [run for run in driver.transaction.runs if "MERGE (f:GraphFact" in run.query]
    assert len(fact_runs) == 2
    assert fact_runs[0].parameters["fact_key"] == fact_runs[1].parameters["fact_key"]
    assert {run.parameters["segment_id"] for run in fact_runs} == {"segment-1", "segment-2"}
    assert all("EVIDENCE_FOR" in run.query for run in fact_runs)


def test_write_queries_carry_full_tenant_document_and_version_scope():
    driver = _FakeDriver()

    _write(driver, "segment-1")

    scoped_runs = [run for run in driver.transaction.runs if run.parameters]
    assert scoped_runs
    for run in scoped_runs:
        assert run.parameters["tenant_id"] == "tenant-1"
        assert run.parameters["dataset_id"] == "dataset-1"
        assert run.parameters["document_id"] == "document-1"
        assert run.parameters["graph_version"] == "v2"
        assert run.parameters["source_version"] == "source-v3"
        assert run.parameters["index_node_id"] == DATASET_GRAPH_INDEX_SCOPE


def test_write_segment_prefixes_entity_attributes_without_touching_facts():
    driver = _FakeDriver()

    _write(driver, "segment-1")

    entity_runs = [run for run in driver.transaction.runs if "MERGE (e:GraphEntity" in run.query]
    assert len(entity_runs) == 2
    dify_run = next(run for run in entity_runs if run.parameters["entity_name"] == "Dify")
    assert "SET e.updated_at = datetime(), e += $entity_attributes" in dify_run.query
    assert dify_run.parameters["entity_attributes"] == {
        "attr_display_name": "Dify 平台",
        "attr_release_date": date(2026, 7, 24),
    }
    assert all(name.startswith("attr_") for name in dify_run.parameters["entity_attributes"])
    assert all(
        name not in dify_run.parameters["entity_attributes"]
        for name in ("tenant_id", "created_at", "updated_at")
    )

    fact_runs = [run for run in driver.transaction.runs if "MERGE (f:GraphFact" in run.query]
    assert len(fact_runs) == 1
    assert "entity_attributes" not in fact_runs[0].parameters


def test_repeated_segment_write_uses_merge_for_nodes_facts_and_evidence():
    driver = _FakeDriver()

    _write(driver, "segment-1")

    queries = "\n".join(run.query for run in driver.transaction.runs)
    assert "MATCH (existing:GraphSegment" in queries
    assert "DETACH DELETE existing" in queries
    assert "MERGE (s:GraphSegment" in queries
    assert "MERGE (e:GraphEntity" in queries
    assert "MERGE (f:GraphFact" in queries
    assert "MERGE (s)-[evidence:EVIDENCE_FOR]->(f)" in queries
    assert "index_node_id: $index_node_id" in queries
    assert "EVIDENCE_FOR {index_node_id: $index_node_id}" in queries
    assert "MENTIONS {index_node_id: $index_node_id}" in queries
    assert "FACT_SOURCE {tenant_id: $tenant_id, dataset_id: $dataset_id" in queries
    assert "FACT_TARGET {tenant_id: $tenant_id, dataset_id: $dataset_id" in queries


def test_finalize_deletes_only_other_document_versions():
    from core.rag.graph_indexing import neo4j_writer as module

    driver = _FakeDriver()
    with patch.object(module, "_get_driver", return_value=driver):
        finalize_document_version(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="document-1",
            graph_version="v2",
            source_version="source-v3",
        )

    assert len(driver.transaction.runs) == 3
    assert {run.query.split("MATCH (node:", 1)[1].split(")", 1)[0] for run in driver.transaction.runs} == {
        "GraphSegment",
        "GraphFact",
        "GraphEntity",
    }
    for cleanup in driver.transaction.runs:
        assert "DETACH DELETE node" in cleanup.query
        assert "node.source_version <> $source_version" in cleanup.query
        assert "node.graph_version <> $graph_version" in cleanup.query
        assert cleanup.parameters == {
            "tenant_id": "tenant-1",
            "dataset_id": "dataset-1",
            "document_id": "document-1",
            "graph_version": "v2",
            "source_version": "source-v3",
            "index_node_id": DATASET_GRAPH_INDEX_SCOPE,
        }


def test_discard_deletes_only_the_exact_document_version():
    from core.rag.graph_indexing import neo4j_writer as module

    driver = _FakeDriver()
    with patch.object(module, "_get_driver", return_value=driver):
        discard_document_version(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="document-1",
            graph_version="v2",
            source_version="source-v3",
        )

    assert len(driver.transaction.runs) == 3
    for cleanup in driver.transaction.runs:
        assert "node.graph_version = $graph_version" in cleanup.query
        assert "node.source_version = $source_version" in cleanup.query
        assert cleanup.parameters["tenant_id"] == "tenant-1"
        assert cleanup.parameters["dataset_id"] == "dataset-1"
        assert cleanup.parameters["document_id"] == "document-1"
        assert cleanup.parameters["index_node_id"] == DATASET_GRAPH_INDEX_SCOPE


def test_reconcile_dataset_graph_deletes_inactive_documents_and_segments_then_orphans():
    from core.rag.graph_indexing import neo4j_writer as module

    driver = _FakeDriver()
    with patch.object(module, "_get_driver", return_value=driver):
        reconcile_dataset_graph(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            active_document_ids=("document-1", "document-2"),
            active_segment_ids=("segment-1", "segment-2"),
        )

    queries = "\n".join(run.query for run in driver.transaction.runs)
    assert "NOT node.document_id IN $active_document_ids" in queries
    assert "NOT node.id IN $active_segment_ids" in queries
    assert "MATCH (fact:GraphFact" in queries
    assert "MATCH (entity:GraphEntity" in queries
    for run in driver.transaction.runs:
        assert run.parameters["tenant_id"] == "tenant-1"
        assert run.parameters["dataset_id"] == "dataset-1"
        assert run.parameters["active_document_ids"] == ["document-1", "document-2"]
        assert run.parameters["active_segment_ids"] == ["segment-1", "segment-2"]
        assert run.parameters["index_node_id"] == DATASET_GRAPH_INDEX_SCOPE


def test_schema_initialization_is_process_idempotent():
    from core.rag.graph_indexing import neo4j_writer as module

    driver = _FakeDriver()
    with (
        patch.object(module, "_get_driver", return_value=driver),
        patch.object(module, "_schema_initialized", False),
    ):
        ensure_graph_schema()
        first_count = len(driver.schema_queries)
        ensure_graph_schema()

    assert first_count >= 6
    assert len(driver.schema_queries) == first_count
    assert any("GraphEntity" in query and "IS UNIQUE" in query for query in driver.schema_queries)
    assert any("GraphFact" in query and "IS UNIQUE" in query for query in driver.schema_queries)
    assert any("SET n.index_node_id = '__dataset__'" in query for query in driver.schema_queries)
    assert any("DROP CONSTRAINT graph_entity_identity" in query for query in driver.schema_queries)
    assert any("GraphSegment" in query and "IS UNIQUE" in query for query in driver.schema_queries)


def test_lifecycle_cleanup_uses_node_scope_to_avoid_cross_scope_deletion():
    from core.rag.graph_indexing import neo4j_writer as module

    driver = _FakeDriver()
    with patch.object(module, "_get_driver", return_value=driver):
        reconcile_dataset_graph(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            active_document_ids=("document-1",),
            active_segment_ids=("segment-1",),
            index_node_id="knowledge-node-1",
        )

    for run in driver.transaction.runs:
        assert run.parameters["index_node_id"] == "knowledge-node-1"
    queries = "\n".join(run.query for run in driver.transaction.runs)
    assert "node.index_node_id = $index_node_id" in queries
    assert "EVIDENCE_FOR {tenant_id: $tenant_id, dataset_id: $dataset_id, " in queries
    assert "FACT_SOURCE {tenant_id: $tenant_id, dataset_id: $dataset_id, " in queries
    assert "FACT_TARGET {tenant_id: $tenant_id, dataset_id: $dataset_id, " in queries
