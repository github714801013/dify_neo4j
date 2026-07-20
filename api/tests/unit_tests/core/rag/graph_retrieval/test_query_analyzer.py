"""Graph Query Analyzer 的结构化输出与降级测试。"""

from unittest.mock import MagicMock, patch

from core.rag.graph.entities import GraphExtractModelConfig, GraphQueryDirection, GraphSchema
from core.rag.graph_retrieval.query_analyzer import analyze_graph_query


def _model() -> GraphExtractModelConfig:
    return GraphExtractModelConfig(provider="provider", model="model")


def test_query_model_uses_configured_max_tokens():
    from core.rag.graph_retrieval import query_analyzer as module

    model_instance = MagicMock()
    response = MagicMock()
    response.message.get_text_content.return_value = "{}"
    model_instance.invoke_llm.return_value = response
    model_manager = MagicMock()
    model_manager.get_model_instance.return_value = model_instance
    model_config = GraphExtractModelConfig(provider="provider", model="model", max_tokens=32428)

    with patch.object(module.ModelManager, "for_tenant", return_value=model_manager):
        module._invoke_query_model(
            tenant_id="tenant-1",
            query="Dify contains Knowledge",
            model_config=model_config,
            schema=GraphSchema.default(),
        )

    assert model_instance.invoke_llm.call_args.kwargs["model_parameters"] == {
        "temperature": 0,
        "max_tokens": 32428,
    }


def test_analyzer_filters_unknown_relations_deduplicates_and_limits_entities():
    from core.rag.graph_retrieval import query_analyzer as module

    payload = {
        "entity_names": ["Dify", "dify", "Knowledge", "API", "Error", "Version", "Ignored"],
        "relation_types": ["CONTAINS", "UNKNOWN", "CONTAINS"],
        "direction": "outbound",
    }
    with patch.object(module, "_invoke_query_model", return_value=payload):
        result = analyze_graph_query(
            tenant_id="tenant-1",
            query="Dify contains Knowledge",
            model_config=_model(),
            schema=GraphSchema.default(),
            limit=10,
        )

    assert result is not None
    assert result.entity_names == ["Dify", "Knowledge", "API", "Error", "Version"]
    assert result.relation_types == ["CONTAINS"]
    assert result.direction == GraphQueryDirection.OUTBOUND
    assert result.limit == 10


def test_analyzer_uses_lexical_fallback_when_model_fails():
    from core.rag.graph_retrieval import query_analyzer as module

    with (
        patch.object(module, "_invoke_query_model", side_effect=RuntimeError("model unavailable")),
        patch.object(module, "_extract_lexical_candidates", return_value=["Dify", "Knowledge"]),
    ):
        result = analyze_graph_query(
            tenant_id="tenant-1",
            query="Dify 知识库如何工作",
            model_config=_model(),
            schema=GraphSchema.default(),
            limit=4,
        )

    assert result is not None
    assert result.entity_names == ["Dify", "Knowledge"]
    assert result.relation_types == []
    assert result.direction == GraphQueryDirection.BOTH


def test_lexical_fallback_extracts_mixed_language_technical_term():
    from core.rag.graph_retrieval import query_analyzer as module

    with patch.object(module, "_invoke_query_model", side_effect=RuntimeError("model unavailable")):
        result = analyze_graph_query(
            tenant_id="tenant-1",
            query="Dify 有哪些 Knowledge 模块？",
            model_config=_model(),
            schema=GraphSchema.default(),
            limit=4,
        )

    assert result is not None
    assert any(item.casefold() == "dify" for item in result.entity_names)


def test_analyzer_returns_none_when_no_entity_candidate_exists():
    from core.rag.graph_retrieval import query_analyzer as module

    with (
        patch.object(module, "_invoke_query_model", return_value={"entity_names": []}),
        patch.object(module, "_extract_lexical_candidates", return_value=[]),
    ):
        result = analyze_graph_query(
            tenant_id="tenant-1",
            query="?",
            model_config=_model(),
            schema=GraphSchema.default(),
            limit=4,
        )

    assert result is None
