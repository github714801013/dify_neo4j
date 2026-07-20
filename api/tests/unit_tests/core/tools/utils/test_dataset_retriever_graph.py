"""Agent Dataset Tool 的 GraphRAG 候选接入测试。"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.app.app_config.entities import DatasetRetrieveConfigEntity
from core.rag.models.document import Document as RagDocument
from core.tools.utils.dataset_retriever import dataset_multi_retriever_tool as multi_module
from core.tools.utils.dataset_retriever import dataset_retriever_tool as single_module
from core.tools.utils.dataset_retriever.dataset_multi_retriever_tool import DatasetMultiRetrieverTool
from core.tools.utils.dataset_retriever.dataset_retriever_tool import DatasetRetrieverTool


def _retrieve_config() -> DatasetRetrieveConfigEntity:
    return DatasetRetrieveConfigEntity(
        retrieve_strategy=DatasetRetrieveConfigEntity.RetrieveStrategy.SINGLE,
        metadata_filtering_mode="disabled",
    )


def _graph_document() -> RagDocument:
    return RagDocument(
        page_content="Dify contains a Knowledge module.",
        provider="dify",
        metadata={
            "doc_id": "graph-node-1",
            "segment_id": "segment-1",
            "document_id": "document-1",
            "dataset_id": "dataset-1",
            "score": 1.0,
            "retrieval_source": "graph",
        },
    )


def test_single_dataset_tool_uses_graph_only_candidate_as_answer_context():
    dataset = SimpleNamespace(
        id="dataset-1",
        tenant_id="tenant-1",
        name="Knowledge",
        provider="dify",
        indexing_technique="high_quality",
        retrieval_model={
            "search_method": "semantic_search",
            "score_threshold_enabled": False,
            "reranking_enable": False,
        },
    )
    segment = SimpleNamespace(
        id="segment-1",
        dataset_id="dataset-1",
        document_id="document-1",
        answer=None,
        get_sign_content=lambda: "Dify contains a Knowledge module.",
    )
    graph_document = _graph_document()
    dataset_retrieval = MagicMock()
    dataset_retrieval.get_metadata_filter_condition.return_value = (None, None)
    dataset_retrieval.augment_with_graph.return_value = [graph_document]
    db_session = MagicMock()
    db_session.scalar.return_value = dataset
    tool = DatasetRetrieverTool(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        retrieve_config=_retrieve_config(),
        return_resource=False,
        retriever_from="prod",
        hit_callbacks=[],
        inputs={},
        top_k=4,
    )

    with (
        patch.object(single_module, "db", SimpleNamespace(session=db_session)),
        patch.object(single_module, "DatasetRetrieval", return_value=dataset_retrieval),
        patch.object(single_module.RetrievalService, "retrieve", return_value=[]),
        patch.object(
            single_module.RetrievalService,
            "format_retrieval_documents",
            return_value=[SimpleNamespace(segment=segment, score=1.0, summary=None)],
        ),
    ):
        result = tool.run(session=MagicMock(), query="Dify 有哪些模块？")

    assert result == "Dify contains a Knowledge module."
    dataset_retrieval.augment_with_graph.assert_called_once()
    assert dataset_retrieval.augment_with_graph.call_args.kwargs["base_documents"] == []


def test_multi_dataset_tool_adds_graph_only_candidate_before_global_reranker():
    dataset = SimpleNamespace(
        id="dataset-1",
        tenant_id="tenant-1",
        indexing_technique="high_quality",
        retrieval_model={
            "search_method": "semantic_search",
            "top_k": 6,
            "score_threshold_enabled": False,
            "reranking_enable": False,
        },
    )
    graph_document = _graph_document()
    dataset_retrieval = MagicMock()
    dataset_retrieval.augment_with_graph.return_value = [graph_document]
    db_session = MagicMock()
    db_session.scalar.return_value = dataset
    all_documents: list[RagDocument] = []
    tool = DatasetMultiRetrieverTool(
        tenant_id="tenant-1",
        dataset_ids=["dataset-1"],
        reranking_provider_name="provider",
        reranking_model_name="model",
        return_resource=False,
        retriever_from="prod",
        hit_callbacks=[],
        top_k=2,
    )
    flask_app = SimpleNamespace(app_context=lambda: nullcontext())

    with (
        patch.object(multi_module, "db", SimpleNamespace(session=db_session)),
        patch.object(multi_module, "DatasetRetrieval", return_value=dataset_retrieval),
        patch.object(multi_module.RetrievalService, "retrieve", return_value=[]),
    ):
        tool._retriever(
            flask_app=flask_app,
            dataset_id="dataset-1",
            query="Dify 有哪些模块？",
            all_documents=all_documents,
            hit_callbacks=[],
        )

    assert all_documents == [graph_document]
    dataset_retrieval.augment_with_graph.assert_called_once()
    assert dataset_retrieval.augment_with_graph.call_args.kwargs["reranking_enable"] is False
