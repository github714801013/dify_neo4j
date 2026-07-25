"""LLM 实体抽取器的 schema 过滤逻辑单元测试。"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from core.rag.graph.entities import DEFAULT_GRAPH_SCHEMA, GraphPropertyDefinition, GraphSchema
from core.rag.graph_indexing.extractor import (
    ExtractedEntity,
    GraphExtractionError,
    _filter_by_schema,
    extract_with_llm,
)
from core.rag.graph_indexing.prompts import build_user_prompt


def _schema() -> GraphSchema:
    """返回 DEFAULT_SCHEMA 的独立副本，避免测试间相互污染。"""
    return DEFAULT_GRAPH_SCHEMA.model_validate(DEFAULT_GRAPH_SCHEMA.model_dump(mode="json"))


def _property_schema() -> GraphSchema:
    return GraphSchema(
        entity_types=["PRODUCT", "MODULE"],
        relation_types=["CONTAINS"],
        allowed_triples=[("PRODUCT", "CONTAINS", "MODULE")],
        entity_properties={
            "PRODUCT": [
                GraphPropertyDefinition(
                    name="display_name", description="产品名称", value_type="string", required=True
                ),
                GraphPropertyDefinition(name="price", description="产品价格", value_type="number"),
                GraphPropertyDefinition(name="is_active", description="是否启用", value_type="boolean"),
                GraphPropertyDefinition(name="release_date", description="发布日期", value_type="date"),
            ],
            "MODULE": [
                GraphPropertyDefinition(name="owner", description="负责团队", value_type="string", required=True),
            ],
        },
    )


def _extract_from_llm_output(raw_output: str):
    response = MagicMock()
    response.message.get_text_content.return_value = raw_output
    model_instance = MagicMock()
    model_instance.invoke_llm.return_value = response

    with patch("core.rag.graph_indexing.extractor.ModelManager.for_tenant") as mock_for_tenant:
        mock_for_tenant.return_value.get_model_instance.return_value = model_instance
        return extract_with_llm(
            tenant_id="tenant-id",
            provider="provider",
            model="model",
            temperature=0,
            segment_text="Dify 包含知识库模块。",
            schema=_schema(),
        )


@pytest.mark.parametrize(
    "raw_output",
    [
        '{"entities": [{"name": "Dify", "type": "PRODUCT"}], "relations": []}',
        '```json\n{"entities": [{"name": "Dify", "type": "PRODUCT"}], "relations": []}\n```',
        '结果如下：\n{"entities": [{"name": "Dify", "type": "PRODUCT"}], "relations": []}\n以上。',
        json.dumps('{"entities": [{"name": "Dify", "type": "PRODUCT"}], "relations": []}'),
    ],
)
def test_extract_with_llm_accepts_json_object_output_variants(raw_output: str):
    result = _extract_from_llm_output(raw_output)

    assert result.entities == [ExtractedEntity(name="Dify", entity_type="PRODUCT")]
    assert result.triples == []


@pytest.mark.parametrize("raw_output", ['[{"entities": []}]', "not json"])
def test_extract_with_llm_rejects_non_object_output(raw_output: str):
    with pytest.raises(GraphExtractionError, match="llm output is not a json object"):
        _extract_from_llm_output(raw_output)


def test_extract_with_llm_applies_schema_filter_after_parsing():
    raw_output = json.dumps(
        {
            "entities": [
                {"name": "Dify", "type": "PRODUCT"},
                {"name": "Ghost", "type": "UNKNOWN"},
            ],
            "relations": [],
        }
    )

    result = _extract_from_llm_output(raw_output)

    assert [entity.name for entity in result.entities] == ["Dify"]


def test_build_user_prompt_requires_typed_entity_properties_and_empty_relation_properties():
    prompt = build_user_prompt("Dify 包含知识库模块。", _property_schema())

    assert '"properties": {"<declared property name>": "<typed value>"}' in prompt
    assert "- PRODUCT: display_name (string, 必填): 产品名称; price (number, 可选): 产品价格" in prompt
    assert "is_active (boolean, 可选): 是否启用" in prompt
    assert "release_date (date, 可选): 发布日期" in prompt
    assert "属性值必须是对应 JSON 标量类型" in prompt
    assert "缺少必填属性的实体不要输出" in prompt
    assert '"properties": {}' in prompt
    assert "relations 的 properties 当前必须为空对象，不要输出关系属性" in prompt


def test_filter_keeps_valid_triples_and_drops_unknown_entity_type():
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Core", "type": "MODULE"},
            {"name": "Ghost", "type": "UNKNOWN"},
        ],
        "relations": [
            {"source": "Dify", "type": "CONTAINS", "target": "Core"},
        ],
    }

    result = _filter_by_schema(payload, _schema())

    assert result.entities == [
        ExtractedEntity(name="Dify", entity_type="PRODUCT"),
        ExtractedEntity(name="Core", entity_type="MODULE"),
    ]
    assert len(result.triples) == 1
    triple = result.triples[0]
    assert (triple.source, triple.relation, triple.target) == ("Dify", "CONTAINS", "Core")


def test_filter_keeps_declared_nested_properties_with_strict_scalar_types():
    payload = {
        "entities": [
            {
                "name": "Dify",
                "type": "PRODUCT",
                "properties": {
                    "display_name": "Dify 平台",
                    "price": 99.5,
                    "is_active": True,
                    "release_date": "2026-07-24",
                    "unconfigured": "discarded",
                },
            },
            {"name": "Knowledge", "type": "MODULE", "properties": {"owner": "RAG 团队"}},
        ],
        "relations": [{"source": "Dify", "type": "CONTAINS", "target": "Knowledge"}],
    }

    result = _filter_by_schema(payload, _property_schema())

    assert result.entities == [
        ExtractedEntity(
            name="Dify",
            entity_type="PRODUCT",
            properties={
                "display_name": "Dify 平台",
                "price": 99.5,
                "is_active": True,
                "release_date": date(2026, 7, 24),
            },
        ),
        ExtractedEntity(name="Knowledge", entity_type="MODULE", properties={"owner": "RAG 团队"}),
    ]
    assert len(result.triples) == 1


@pytest.mark.parametrize(
    ("properties", "expected_entities"),
    [
        (
            {"display_name": "Dify", "price": "99", "is_active": "true", "release_date": "2026-02-30"},
            [ExtractedEntity(name="Dify", entity_type="PRODUCT", properties={"display_name": "Dify"})],
        ),
        (
            {"display_name": "Dify", "price": float("inf"), "is_active": 1, "release_date": []},
            [ExtractedEntity(name="Dify", entity_type="PRODUCT", properties={"display_name": "Dify"})],
        ),
        (
            {"display_name": "Dify", "price": {}, "is_active": [], "release_date": {}},
            [ExtractedEntity(name="Dify", entity_type="PRODUCT", properties={"display_name": "Dify"})],
        ),
        (
            {"display_name": "Dify", "price": True, "is_active": False, "release_date": "2026-7-1"},
            [
                ExtractedEntity(
                    name="Dify", entity_type="PRODUCT", properties={"display_name": "Dify", "is_active": False}
                )
            ],
        ),
        ({"display_name": "", "price": 10, "is_active": True, "release_date": "2026-07-01"}, []),
    ],
)
def test_filter_drops_invalid_properties_without_coercion(
    properties: dict[str, object], expected_entities: list[ExtractedEntity]
):
    payload = {"entities": [{"name": "Dify", "type": "PRODUCT", "properties": properties}], "relations": []}

    assert _filter_by_schema(payload, _property_schema()).entities == expected_entities


def test_filter_skips_only_entity_when_required_property_is_missing_or_invalid_and_drops_its_relations():
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT", "properties": {"display_name": "Dify"}},
            {"name": "Incomplete", "type": "MODULE", "properties": {"owner": ""}},
            {"name": "Knowledge", "type": "MODULE", "properties": {"owner": "RAG 团队"}},
        ],
        "relations": [
            {"source": "Dify", "type": "CONTAINS", "target": "Incomplete"},
            {"source": "Dify", "type": "CONTAINS", "target": "Knowledge"},
        ],
    }

    result = _filter_by_schema(payload, _property_schema())

    assert [entity.name for entity in result.entities] == ["Dify", "Knowledge"]
    assert [(triple.source, triple.relation, triple.target) for triple in result.triples] == [
        ("Dify", "CONTAINS", "Knowledge")
    ]


def test_filter_drops_relation_to_missing_entity():
    payload = {
        "entities": [{"name": "Dify", "type": "PRODUCT"}],
        "relations": [
            {"source": "Dify", "type": "CONTAINS", "target": "NotExist"},
        ],
    }
    result = _filter_by_schema(payload, _schema())
    assert result.entities == [ExtractedEntity(name="Dify", entity_type="PRODUCT")]
    assert result.triples == []


def test_filter_drops_triple_not_in_allowed_triples():
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Core", "type": "MODULE"},
        ],
        "relations": [
            {"source": "Dify", "type": "CALLS", "target": "Core"},
        ],
    }
    assert _filter_by_schema(payload, _schema()).triples == []


def test_filter_drops_blank_and_duplicate_entities():
    payload = {
        "entities": [
            {"name": "", "type": "PRODUCT"},
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Dify", "type": "MODULE"},
        ],
        "relations": [],
    }
    assert _filter_by_schema(payload, _schema()).entities == [ExtractedEntity(name="Dify", entity_type="PRODUCT")]


def test_filter_handles_empty_and_malformed_payload():
    assert _filter_by_schema({}, _schema()).entities == []
    assert _filter_by_schema({"entities": "not-a-list"}, _schema()).triples == []
    assert _filter_by_schema({"entities": [], "relations": []}, _schema()).entities == []
    assert _filter_by_schema({"entities": [], "relations": ["bad"]}, _schema()).triples == []


def test_filter_dedups_triples_by_values():
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Core", "type": "MODULE"},
        ],
        "relations": [
            {"source": "Dify", "type": "CONTAINS", "target": "Core"},
            {"source": "Dify", "type": "CONTAINS", "target": "Core"},
        ],
    }
    assert len(_filter_by_schema(payload, _schema()).triples) == 1


def test_filter_limits_triplets_after_deduplication():
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Core", "type": "MODULE"},
            {"name": "Knowledge", "type": "MODULE"},
        ],
        "relations": [
            {"source": "Dify", "type": "CONTAINS", "target": "Core"},
            {"source": "Dify", "type": "CONTAINS", "target": "Core"},
            {"source": "Dify", "type": "CONTAINS", "target": "Knowledge"},
        ],
    }

    result = _filter_by_schema(payload, _schema(), max_triplets_per_chunk=1)

    assert [(triple.source, triple.relation, triple.target) for triple in result.triples] == [
        ("Dify", "CONTAINS", "Core")
    ]


def test_non_strict_mode_allows_declared_types_outside_allowed_triples():
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Core", "type": "MODULE"},
        ],
        "relations": [
            {"source": "Dify", "type": "CALLS", "target": "Core"},
        ],
    }

    result = _filter_by_schema(payload, _schema(), strict=False)

    assert [(triple.source, triple.relation, triple.target) for triple in result.triples] == [("Dify", "CALLS", "Core")]
