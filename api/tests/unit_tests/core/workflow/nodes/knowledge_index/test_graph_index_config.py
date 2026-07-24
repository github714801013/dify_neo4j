"""Knowledge Base 节点图谱索引配置的契约测试。"""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from core.workflow.nodes.knowledge_index.entities import KnowledgeIndexNodeData
from services.entities.knowledge_entities.rag_pipeline_entities import KnowledgeConfiguration

VALID_GRAPH_INDEX_CONFIG = {
    "enabled": True,
    "schema": {
        "entity_types": [
            {"name": "PRODUCT", "properties": []},
            {"name": "MODULE", "properties": []},
        ],
        "relation_types": [{"name": "CONTAINS", "properties": []}],
        "allowed_triples": [
            {
                "source_type": "PRODUCT",
                "relation_type": "CONTAINS",
                "target_type": "MODULE",
            }
        ],
    },
    "extract_model_config": {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "temperature": 0,
        "max_tokens": 2048,
        "max_triplets_per_chunk": 10,
        "strict": True,
    },
}


def _node_data(**overrides: object) -> dict[str, object]:
    return {
        "type": "knowledge-index",
        "chunk_structure": "text_model",
        "index_chunk_variable_selector": [],
        **overrides,
    }


def _knowledge_configuration(**overrides: object) -> dict[str, object]:
    return {
        "chunk_structure": "text_model",
        "indexing_technique": "high_quality",
        "retrieval_model": {
            "search_method": "semantic_search",
            "top_k": 3,
        },
        **overrides,
    }


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (KnowledgeIndexNodeData.model_validate, _node_data()),
        (KnowledgeConfiguration.model_validate, _knowledge_configuration()),
    ],
)
def test_existing_knowledge_base_configuration_remains_compatible(factory, payload: dict[str, object]):
    assert factory(payload).graph_index_config is None


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (KnowledgeIndexNodeData.model_validate, _node_data(graph_index_config={"enabled": False})),
        (KnowledgeConfiguration.model_validate, _knowledge_configuration(graph_index_config={"enabled": False})),
    ],
)
def test_disabled_graph_index_config_allows_the_minimal_payload(factory, payload: dict[str, object]):
    config = factory(payload).graph_index_config

    assert config is not None
    assert config.model_dump(exclude_none=True) == {"enabled": False}


@pytest.mark.parametrize("missing_field", ["schema", "extract_model_config"])
def test_enabled_graph_index_config_requires_schema_and_extract_model(missing_field: str):
    graph_index_config = deepcopy(VALID_GRAPH_INDEX_CONFIG)
    del graph_index_config[missing_field]

    with pytest.raises(ValidationError):
        KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=graph_index_config))


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (KnowledgeIndexNodeData.model_validate, _node_data(graph_rag_config={"enabled": False})),
        (KnowledgeConfiguration.model_validate, _knowledge_configuration(graph_rag_config={"enabled": False})),
    ],
)
def test_graph_rag_config_alias_is_rejected(factory, payload: dict[str, object]):
    with pytest.raises(ValidationError, match="graph_rag_config"):
        factory(payload)


@pytest.mark.parametrize(
    "graph_index_config",
    [
        {**VALID_GRAPH_INDEX_CONFIG, "unexpected": "value"},
        {
            **VALID_GRAPH_INDEX_CONFIG,
            "schema": {**VALID_GRAPH_INDEX_CONFIG["schema"], "unexpected": "value"},
        },
        {
            **VALID_GRAPH_INDEX_CONFIG,
            "extract_model_config": {**VALID_GRAPH_INDEX_CONFIG["extract_model_config"], "api_key": "secret"},
        },
    ],
)
def test_graph_index_config_rejects_unknown_fields(graph_index_config: dict[str, object]):
    with pytest.raises(ValidationError):
        KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=graph_index_config))


@pytest.mark.parametrize(
    "schema",
    [
        {
            "entity_types": [
                {"name": " PRODUCT ", "properties": []},
                {"name": "PRODUCT", "properties": []},
            ],
            "relation_types": [{"name": "CONTAINS", "properties": []}],
            "allowed_triples": [],
        },
        {
            "entity_types": [{"name": "PRODUCT", "properties": []}],
            "relation_types": [{"name": " ", "properties": []}],
            "allowed_triples": [],
        },
        {
            "entity_types": [{"name": "PRODUCT", "properties": []}],
            "relation_types": [{"name": "CONTAINS", "properties": [{"name": "future"}]}],
            "allowed_triples": [],
        },
        {
            "entity_types": [{"name": "PRODUCT", "properties": []}],
            "relation_types": [{"name": "CONTAINS", "properties": []}],
            "allowed_triples": [
                {"source_type": "PRODUCT", "relation_type": "CONTAINS", "target_type": "MODULE"},
            ],
        },
        {
            "entity_types": [{"name": "PRODUCT", "properties": []}],
            "relation_types": [{"name": "CONTAINS", "properties": []}],
            "allowed_triples": [
                {"source_type": "PRODUCT", "relation_type": "CONTAINS", "target_type": "PRODUCT"},
                {"source_type": "PRODUCT", "relation_type": "CONTAINS", "target_type": "PRODUCT"},
            ],
        },
    ],
)
def test_graph_index_schema_rejects_invalid_names_properties_and_triples(schema: dict[str, object]):
    graph_index_config = {**VALID_GRAPH_INDEX_CONFIG, "schema": schema}

    with pytest.raises(ValidationError):
        KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=graph_index_config))


def test_graph_version_ignores_schema_list_order_and_is_read_only():
    first = KnowledgeIndexNodeData.model_validate(
        _node_data(graph_index_config=VALID_GRAPH_INDEX_CONFIG)
    ).graph_index_config
    reordered = deepcopy(VALID_GRAPH_INDEX_CONFIG)
    reordered["schema"]["entity_types"].reverse()
    second = KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=reordered)).graph_index_config

    assert first is not None
    assert second is not None
    assert first.graph_version == second.graph_version
    assert first.graph_version is not None


def test_graph_version_changes_when_extract_model_configuration_changes():
    first = KnowledgeIndexNodeData.model_validate(
        _node_data(graph_index_config=VALID_GRAPH_INDEX_CONFIG)
    ).graph_index_config
    changed = deepcopy(VALID_GRAPH_INDEX_CONFIG)
    changed["extract_model_config"]["max_triplets_per_chunk"] = 11
    second = KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=changed)).graph_index_config

    assert first is not None
    assert second is not None
    assert first.graph_version != second.graph_version


@pytest.mark.parametrize(
    "graph_index_config",
    [
        {"enabled": False, "graph_version": "stale"},
        {"enabled": False, "schema": VALID_GRAPH_INDEX_CONFIG["schema"]},
    ],
)
def test_disabled_graph_index_config_rejects_non_minimal_payload(graph_index_config: dict[str, object]):
    with pytest.raises(ValidationError, match="disabled graph_index_config"):
        KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=graph_index_config))


def test_graph_version_is_recomputed_instead_of_accepting_a_client_value():
    graph_index_config = {**VALID_GRAPH_INDEX_CONFIG, "graph_version": "client-controlled"}

    config = KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=graph_index_config)).graph_index_config

    assert config is not None
    assert config.graph_version != "client-controlled"


def test_entity_property_definitions_accept_supported_scalar_types_and_change_graph_version():
    configured = deepcopy(VALID_GRAPH_INDEX_CONFIG)
    configured["schema"]["entity_types"][0]["properties"] = [
        {"name": "display_name", "description": "产品名称", "value_type": "string", "required": True},
        {"name": "release_date", "description": "发布日期", "value_type": "date", "required": False},
        {"name": "price", "description": "价格", "value_type": "number", "required": False},
        {"name": "is_active", "description": "是否启用", "value_type": "boolean", "required": False},
    ]

    config = KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=configured)).graph_index_config
    baseline = KnowledgeIndexNodeData.model_validate(
        _node_data(graph_index_config=VALID_GRAPH_INDEX_CONFIG)
    ).graph_index_config

    assert config is not None
    assert baseline is not None
    assert config.schema is not None
    assert config.schema.entity_types[0].properties[0].name == "display_name"
    assert config.graph_version != baseline.graph_version


@pytest.mark.parametrize(
    "property_definition",
    [
        {"name": "DisplayName", "description": "名称", "value_type": "string", "required": False},
        {"name": "display-name", "description": "名称", "value_type": "string", "required": False},
        {"name": "1display_name", "description": "名称", "value_type": "string", "required": False},
        {"name": "display_name", "description": " ", "value_type": "string", "required": False},
        {"name": "display_name", "description": "名称", "value_type": "array", "required": False},
        {"name": "display_name", "description": "名称", "value_type": "string", "required": 1},
    ],
)
def test_entity_property_definitions_reject_invalid_contracts(property_definition: dict[str, object]):
    configured = deepcopy(VALID_GRAPH_INDEX_CONFIG)
    configured["schema"]["entity_types"][0]["properties"] = [property_definition]

    with pytest.raises(ValidationError):
        KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=configured))


def test_entity_property_definitions_reject_duplicate_names_in_one_entity_type():
    configured = deepcopy(VALID_GRAPH_INDEX_CONFIG)
    configured["schema"]["entity_types"][0]["properties"] = [
        {"name": "display_name", "description": "产品名称", "value_type": "string", "required": False},
        {"name": "display_name", "description": "产品别名", "value_type": "string", "required": False},
    ]

    with pytest.raises(ValidationError, match="graph schema property names must not contain duplicates"):
        KnowledgeIndexNodeData.model_validate(_node_data(graph_index_config=configured))
