from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from controllers.console.datasets.datasets import (
    GraphRagConfigPayload,
    _get_graph_extraction_config,
    _get_graph_retrieval_config,
    _save_graph_extraction_config,
)
from core.rag.graph.entities import (
    DEFAULT_DOCUMENT_GRAPH_SCHEMA,
    DEFAULT_GRAPH_SCHEMA,
    LEGACY_DOCUMENT_GRAPH_SCHEMA,
    GraphExtractionConfig,
    GraphRetrievalConfig,
)


def test_graph_rag_config_accepts_llama_index_extractor_settings():
    payload = GraphRagConfigPayload(
        enabled=True,
        query_mode="hybrid",
        graph_top_k=10,
        graph_max_depth=1,
        graph_timeout_ms=1500,
        graph_weight=0.3,
        extract_model_config={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "temperature": 0.1,
            "max_triplets_per_chunk": 10,
            "strict": True,
        },
    )

    assert payload.extract_model_config is not None
    assert payload.extract_model_config.max_triplets_per_chunk == 10


@pytest.mark.parametrize(
    "config",
    [
        {"provider": "openai", "model": "", "temperature": 0.1, "max_triplets_per_chunk": 10, "strict": True},
        {"provider": "openai", "model": "model", "temperature": 2.1, "max_triplets_per_chunk": 10, "strict": True},
        {"provider": "openai", "model": "model", "temperature": 0.1, "max_triplets_per_chunk": 51, "strict": True},
    ],
)
def test_graph_rag_config_rejects_invalid_llama_index_settings(config):
    with pytest.raises(ValidationError):
        GraphRagConfigPayload(extract_model_config=config)


def test_graph_rag_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        GraphRagConfigPayload(extract_model_config={
            "provider": "openai",
            "model": "model",
            "unknown": True,
        })


def test_missing_dataset_graph_config_returns_independent_retrieval_and_extraction_defaults():
    """首次配置的图检索关闭，图谱抽取草稿预填可编辑的文档 Schema。"""
    session = MagicMock()
    session.scalar.return_value = None

    retrieval = _get_graph_retrieval_config("tenant-1", "dataset-1", session=session)
    extraction = _get_graph_extraction_config("tenant-1", "dataset-1", session=session)

    assert retrieval == GraphRetrievalConfig().model_dump(mode="json")
    assert extraction == GraphExtractionConfig(
        enabled=False,
        schema=DEFAULT_DOCUMENT_GRAPH_SCHEMA,
    ).model_dump(mode="json")


def test_legacy_uppercase_default_schema_is_presented_as_document_schema():
    session = MagicMock()
    session.scalar.return_value = MagicMock(
        extract_model_config=None,
        index_enabled=False,
        schema_json=DEFAULT_GRAPH_SCHEMA.model_dump(mode="json"),
        is_graph_indexing_enabled=False,
        graph_version="v1",
    )

    extraction = _get_graph_extraction_config("tenant-1", "dataset-1", session=session)

    assert extraction["schema"] == DEFAULT_DOCUMENT_GRAPH_SCHEMA.model_dump(mode="json")


def test_legacy_person_document_schema_is_presented_as_module_document_schema():
    session = MagicMock()
    session.scalar.return_value = MagicMock(
        extract_model_config=None,
        index_enabled=False,
        schema_json=LEGACY_DOCUMENT_GRAPH_SCHEMA.model_dump(mode="json"),
        is_graph_indexing_enabled=False,
        graph_version="v1",
    )

    extraction = _get_graph_extraction_config("tenant-1", "dataset-1", session=session)

    assert extraction["schema"] == DEFAULT_DOCUMENT_GRAPH_SCHEMA.model_dump(mode="json")


def test_saving_enabled_extraction_ignores_client_graph_version():
    """图谱版本由已保存的模型和 Schema 计算，客户端不能指定版本。"""
    session = MagicMock()
    session.scalar.return_value = None
    schema = DEFAULT_DOCUMENT_GRAPH_SCHEMA.model_dump(mode="json")

    _save_graph_extraction_config(
        "tenant-1",
        "dataset-1",
        {
            "enabled": True,
            "schema": schema,
            "extract_model_config": {"provider": "openai", "model": "gpt-4.1-mini"},
            "graph_version": "client-controlled",
        },
        session=session,
    )

    saved = session.add.call_args.args[0]
    expected = GraphExtractionConfig(
        enabled=True,
        schema=DEFAULT_DOCUMENT_GRAPH_SCHEMA,
        extract_model_config={"provider": "openai", "model": "gpt-4.1-mini"},
    )
    assert saved.index_enabled is True
    assert saved.graph_version == expected.graph_version
    assert saved.graph_version != "client-controlled"
