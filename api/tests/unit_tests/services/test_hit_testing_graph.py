"""知识库命中测试页面接入 GraphRAG 候选的测试。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.rag.models.document import Document
from services import hit_testing_service as module
from services.hit_testing_service import HitTestingService


def test_hit_testing_uses_graph_only_candidate_in_compact_response():
    dataset = SimpleNamespace(
        id="dataset-1",
        tenant_id="tenant-1",
        retrieval_model=None,
    )
    account = SimpleNamespace(id="account-1")
    graph_document = Document(
        page_content="Graph-only result",
        provider="dify",
        metadata={
            "doc_id": "graph-node-1",
            "dataset_id": "dataset-1",
            "document_id": "document-1",
            "score": 1.0,
            "retrieval_source": "graph",
        },
    )
    dataset_retrieval = MagicMock()
    dataset_retrieval.augment_with_graph.return_value = [graph_document]
    session = MagicMock()
    expected = {"query": {"content": "GraphRAG"}, "records": [{"graph": True}]}

    with (
        patch.object(module, "DatasetRetrieval", return_value=dataset_retrieval),
        patch.object(module.RetrievalService, "retrieve", return_value=[]),
        patch.object(HitTestingService, "compact_retrieve_response", return_value=expected) as compact,
    ):
        result = HitTestingService.retrieve(
            dataset=dataset,
            query="GraphRAG",
            account=account,
            retrieval_model={
                "search_method": "semantic_search",
                "top_k": 4,
                "score_threshold_enabled": False,
                "reranking_enable": False,
            },
            external_retrieval_model={},
            session=session,
        )

    assert result == expected
    dataset_retrieval.augment_with_graph.assert_called_once()
    compact.assert_called_once_with("GraphRAG", [graph_document], session=session)
