import pytest
from pydantic import ValidationError

from controllers.console.datasets.datasets import GraphRagConfigPayload


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
