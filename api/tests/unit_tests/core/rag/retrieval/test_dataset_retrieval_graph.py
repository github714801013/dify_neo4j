"""Dataset Retrieval 接入 GraphRAG 候选的最小 Hook 测试。"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.rag.graph_retrieval.service import GraphRetrievalBatch
from core.rag.models.document import Document
from core.rag.retrieval.dataset_retrieval import DatasetRetrieval


def _document(doc_id: str, *, score: float = 0.8, source: str = "vector") -> Document:
    return Document(
        page_content=doc_id,
        provider="dify",
        metadata={
            "doc_id": doc_id,
            "dataset_id": "dataset-1",
            "document_id": "document-1",
            "score": score,
            "retrieval_source": source,
        },
    )


def test_augment_no_graph_candidates_preserves_base_documents_without_reranking():
    retrieval = DatasetRetrieval()
    base = [_document("node-1")]

    with (
        patch(
            "core.rag.retrieval.dataset_retrieval.retrieve_graph_documents",
            return_value=GraphRetrievalBatch(documents=[], graph_weight=0.3),
        ),
        patch("core.rag.retrieval.dataset_retrieval.DataPostProcessor") as processor,
    ):
        result = retrieval.augment_with_graph(
            session=MagicMock(),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="Dify",
            base_documents=base,
            top_k=4,
            score_threshold=0,
            reranking_enable=True,
            reranking_mode="reranking_model",
            reranking_model={"reranking_provider_name": "provider", "reranking_model_name": "model"},
            weights=None,
            document_ids_filter=None,
        )

    assert result == base
    processor.assert_not_called()


def test_augment_fuses_graph_candidate_and_sends_it_to_existing_reranker():
    retrieval = DatasetRetrieval()
    base = [_document("vector-node", score=0.9)]
    graph = [_document("graph-node", score=1.0, source="graph")]
    reranked = [graph[0], base[0]]
    processor = MagicMock()
    processor.invoke.return_value = reranked

    with (
        patch(
            "core.rag.retrieval.dataset_retrieval.retrieve_graph_documents",
            return_value=GraphRetrievalBatch(documents=graph, graph_weight=0.7),
        ),
        patch("core.rag.retrieval.dataset_retrieval.DataPostProcessor", return_value=processor) as processor_cls,
    ):
        result = retrieval.augment_with_graph(
            session=MagicMock(),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="GraphRAG",
            base_documents=base,
            top_k=4,
            score_threshold=0.2,
            reranking_enable=True,
            reranking_mode="reranking_model",
            reranking_model={"reranking_provider_name": "provider", "reranking_model_name": "model"},
            weights=None,
            document_ids_filter=["document-1"],
        )

    assert result == reranked
    processor_cls.assert_called_once_with(
        "tenant-1",
        "reranking_model",
        {"reranking_provider_name": "provider", "reranking_model_name": "model"},
        None,
        False,
    )
    rerank_documents = processor.invoke.call_args.kwargs["documents"]
    assert {item.metadata["doc_id"] for item in rerank_documents} == {"vector-node", "graph-node"}
    assert processor.invoke.call_args.kwargs["top_n"] == 4


def test_augment_reranker_error_fails_open_to_original_base_results():
    retrieval = DatasetRetrieval()
    base = [_document("vector-node")]
    graph = [_document("graph-node", source="graph")]
    processor = MagicMock()
    processor.invoke.side_effect = RuntimeError("reranker unavailable")

    from core.rag.retrieval import dataset_retrieval as module

    with (
        patch.object(module.dify_config, "GRAPH_RAG_FAIL_OPEN", True),
        patch.object(
            module,
            "retrieve_graph_documents",
            return_value=GraphRetrievalBatch(documents=graph, graph_weight=0.5),
        ),
        patch.object(module, "DataPostProcessor", return_value=processor),
    ):
        result = retrieval.augment_with_graph(
            session=MagicMock(),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            query="GraphRAG",
            base_documents=base,
            top_k=4,
            score_threshold=0,
            reranking_enable=True,
            reranking_mode="reranking_model",
            reranking_model={"reranking_provider_name": "provider", "reranking_model_name": "model"},
            weights=None,
            document_ids_filter=None,
        )

    assert result == base


def test_augment_reranker_error_propagates_when_fail_open_disabled():
    retrieval = DatasetRetrieval()
    base = [_document("vector-node")]
    graph = [_document("graph-node", source="graph")]
    processor = MagicMock()
    processor.invoke.side_effect = RuntimeError("reranker unavailable")

    from core.rag.retrieval import dataset_retrieval as module

    with (
        patch.object(module.dify_config, "GRAPH_RAG_FAIL_OPEN", False),
        patch.object(
            module,
            "retrieve_graph_documents",
            return_value=GraphRetrievalBatch(documents=graph, graph_weight=0.5),
        ),
        patch.object(module, "DataPostProcessor", return_value=processor),
    ):
        with pytest.raises(RuntimeError, match="reranker unavailable"):
            retrieval.augment_with_graph(
                session=MagicMock(),
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                query="GraphRAG",
                base_documents=base,
                top_k=4,
                score_threshold=0,
                reranking_enable=True,
                reranking_mode="reranking_model",
                reranking_model={"reranking_provider_name": "provider", "reranking_model_name": "model"},
                weights=None,
                document_ids_filter=None,
            )


def test_internal_retriever_appends_graph_only_candidate_to_shared_results():
    retrieval = DatasetRetrieval()
    graph = _document("graph-node", source="graph")
    dataset = SimpleNamespace(
        id="dataset-1",
        tenant_id="tenant-1",
        provider="dify",
        indexing_technique="high_quality",
        retrieval_model={
            "search_method": "semantic_search",
            "top_k": 4,
            "score_threshold_enabled": False,
            "reranking_enable": False,
        },
    )
    session = MagicMock()
    session.scalar.return_value = dataset
    all_documents: list[Document] = []
    flask_app = SimpleNamespace(app_context=lambda: nullcontext())

    with (
        patch("core.rag.retrieval.dataset_retrieval.RetrievalService.retrieve", return_value=[]),
        patch.object(retrieval, "augment_with_graph", return_value=[graph]) as augment,
    ):
        retrieval._retriever(
            flask_app=flask_app,
            session=session,
            dataset_id="dataset-1",
            query="GraphRAG",
            top_k=4,
            all_documents=all_documents,
        )

    assert all_documents == [graph]
    augment.assert_called_once()


def test_single_retrieve_internal_path_calls_graph_augmentation():
    retrieval = DatasetRetrieval()
    dataset = SimpleNamespace(
        id="dataset-1",
        name="Dataset",
        description="desc",
        tenant_id="tenant-1",
        provider="dify",
        indexing_technique="high_quality",
        retrieval_model={
            "search_method": "semantic_search",
            "top_k": 4,
            "score_threshold_enabled": False,
            "reranking_enable": False,
        },
    )
    session = MagicMock()
    session.scalar.return_value = dataset
    graph = _document("graph-node", source="graph")

    from core.rag.retrieval import dataset_retrieval as module

    with (
        patch.object(module, "FunctionCallMultiDatasetRouter") as router_cls,
        patch.object(module.RetrievalService, "retrieve", return_value=[]),
        patch.object(retrieval, "augment_with_graph", return_value=[graph]) as augment,
        patch.object(retrieval, "_on_query"),
        patch.object(retrieval, "_on_retrieval_end"),
        patch.object(module.threading, "Thread") as thread_cls,
    ):
        router_cls.return_value.invoke.return_value = ("dataset-1", SimpleNamespace(total_tokens=0))
        thread_cls.return_value.start.return_value = None
        result = retrieval.single_retrieve(
            session=session,
            app_id="app-1",
            tenant_id="tenant-1",
            user_id="user-1",
            user_from="workflow",
            query="GraphRAG",
            available_datasets=[dataset],
            model_instance=MagicMock(),
            model_config=MagicMock(),
            planning_strategy=module.PlanningStrategy.ROUTER,
        )

    assert result == [graph]
    augment.assert_called_once()
