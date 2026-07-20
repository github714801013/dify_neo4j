"""Graph 与基础检索候选的加权 RRF 融合测试。"""

from core.rag.graph_retrieval.fusion import fuse_retrieval_documents
from core.rag.models.document import Document


def _document(doc_id: str, score: float, *, source: str, segment_id: str | None = None) -> Document:
    metadata: dict[str, object] = {
        "doc_id": doc_id,
        "document_id": "document-1",
        "dataset_id": "dataset-1",
        "score": score,
        "retrieval_source": source,
    }
    if segment_id:
        metadata["segment_id"] = segment_id
    return Document(page_content=doc_id, metadata=metadata, provider="dify")


def test_no_graph_candidates_preserves_upstream_documents_and_scores():
    base = [_document("node-1", 0.91, source="vector"), _document("node-2", 0.82, source="vector")]

    result = fuse_retrieval_documents(base, [], graph_weight=0.3, top_k=2)

    assert result == base
    assert result[0].metadata["score"] == 0.91


def test_graph_only_candidates_are_returned_in_graph_rank_order():
    graph = [
        _document("node-2", 0.5, source="graph", segment_id="segment-2"),
        _document("node-1", 1.0, source="graph", segment_id="segment-1"),
    ]

    result = fuse_retrieval_documents([], graph, graph_weight=0.4, top_k=2)

    assert [item.metadata["doc_id"] for item in result] == ["node-2", "node-1"]
    assert all("graph" in item.metadata["retrieval_sources"] for item in result)


def test_duplicate_segment_is_returned_once_and_keeps_graph_metadata():
    base = [_document("node-1", 0.9, source="vector")]
    graph = [_document("node-1", 1.0, source="graph", segment_id="segment-1")]
    graph[0].metadata.update({"graph_rank": 1, "graph_path": ["Dify -[CONTAINS]-> Knowledge"]})

    result = fuse_retrieval_documents(base, graph, graph_weight=0.3, top_k=4)

    assert len(result) == 1
    assert result[0].page_content == "node-1"
    assert result[0].metadata["graph_rank"] == 1
    assert result[0].metadata["retrieval_sources"] == ["base", "graph"]
    assert result[0].metadata["score"] == 1.0


def test_graph_weight_changes_relative_order_of_base_only_and_graph_only_candidates():
    base = [_document("vector-node", 0.9, source="vector")]
    graph = [_document("graph-node", 1.0, source="graph", segment_id="segment-graph")]

    vector_favored = fuse_retrieval_documents(base, graph, graph_weight=0.2, top_k=2)
    graph_favored = fuse_retrieval_documents(base, graph, graph_weight=0.8, top_k=2)

    assert vector_favored[0].metadata["doc_id"] == "vector-node"
    assert graph_favored[0].metadata["doc_id"] == "graph-node"


def test_zero_graph_weight_does_not_add_graph_only_candidates():
    base = [_document("vector-node", 0.9, source="vector")]
    graph = [_document("graph-node", 1.0, source="graph", segment_id="segment-graph")]

    result = fuse_retrieval_documents(base, graph, graph_weight=0, top_k=4)

    assert result == base
