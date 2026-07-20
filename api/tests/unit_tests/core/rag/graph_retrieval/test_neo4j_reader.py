"""Neo4j Reader 固定模板、租户隔离和结果映射测试。"""

from dataclasses import dataclass, field
from unittest.mock import patch

from core.rag.graph.entities import GraphQuery, GraphQueryDirection
from core.rag.graph_retrieval.neo4j_reader import ActiveGraphVersion, read_graph_results


@dataclass
class _FakeResult:
    rows: list[dict[str, object]]

    def __iter__(self):
        return iter(self.rows)


@dataclass
class _FakeSession:
    rows: list[dict[str, object]]
    calls: list[tuple[object, dict[str, object]]] = field(default_factory=list)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def run(self, query: object, **parameters: object) -> _FakeResult:
        self.calls.append((query, parameters))
        return _FakeResult(self.rows)


@dataclass
class _FakeDriver:
    rows: list[dict[str, object]]
    session_instance: _FakeSession = field(init=False)

    def __post_init__(self):
        self.session_instance = _FakeSession(self.rows)

    def session(self, *, database: str | None = None) -> _FakeSession:
        return self.session_instance


def test_reader_uses_parameterized_fixed_one_hop_query_and_maps_ranked_results():
    from core.rag.graph_retrieval import neo4j_reader as module

    driver = _FakeDriver(
        [
            {
                "segment_id": "segment-1",
                "graph_distance": 1,
                "graph_path": ["Dify -[CONTAINS]-> Knowledge"],
                "matched_entities": ["Dify"],
                "relation_types": ["CONTAINS"],
            },
            {
                "segment_id": "segment-2",
                "graph_distance": 1,
                "graph_path": ["Dify -[SUPPORTS]-> API"],
                "matched_entities": ["Dify"],
                "relation_types": ["SUPPORTS"],
            },
        ]
    )
    graph_query = GraphQuery(
        entity_names=["Dify' OR 1=1"],
        relation_types=["CONTAINS"],
        direction=GraphQueryDirection.BOTH,
        limit=2,
    )

    with (
        patch.object(module, "get_graph_driver", return_value=driver),
        patch.object(module, "get_graph_database_name", return_value="neo4j"),
        patch.object(module, "_query_with_timeout", side_effect=lambda text, _timeout: text),
    ):
        results = read_graph_results(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            graph_version="v2",
            active_versions=(ActiveGraphVersion("document-1", "source-1"),),
            graph_query=graph_query,
            timeout_ms=1500,
        )

    assert [item.segment_id for item in results] == ["segment-1", "segment-2"]
    assert [item.graph_rank for item in results] == [1, 2]
    assert results[0].matched_entities == ["Dify"]
    query_text, parameters = driver.session_instance.calls[0]
    assert "GraphEntity" in str(query_text)
    assert "GraphFact" in str(query_text)
    assert "GraphSegment" in str(query_text)
    assert "Dify' OR 1=1" not in str(query_text)
    assert parameters["entity_names"] == ["dify' or 1=1"]
    assert parameters["tenant_id"] == "tenant-1"
    assert parameters["dataset_id"] == "dataset-1"
    assert parameters["graph_version"] == "v2"
    assert parameters["active_versions"] == [{"document_id": "document-1", "source_version": "source-1"}]


def test_reader_skips_neo4j_when_no_current_successful_version_exists():
    from core.rag.graph_retrieval import neo4j_reader as module

    driver = _FakeDriver([])
    with patch.object(module, "get_graph_driver", return_value=driver):
        results = read_graph_results(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            graph_version="v1",
            active_versions=(),
            graph_query=GraphQuery(entity_names=["Dify"]),
            timeout_ms=1000,
        )

    assert results == []
    assert driver.session_instance.calls == []
