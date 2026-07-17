"""LLM 实体抽取器的 schema 过滤逻辑单元测试。

只覆盖纯函数 ``_filter_by_schema``，不调用真实 LLM；该函数是抽取链路中
最容易产生误判与回归的核心逻辑。
"""

from core.rag.graph.entities import DEFAULT_GRAPH_SCHEMA
from core.rag.graph_indexing.extractor import _filter_by_schema


def _schema():
    """返回 DEFAULT_SCHEMA 的独立副本，避免测试间相互污染。"""
    return DEFAULT_GRAPH_SCHEMA.model_validate(DEFAULT_GRAPH_SCHEMA.model_dump(mode="json"))


def test_filter_keeps_valid_triples_and_drops_unknown_entity_type():
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Core", "type": "MODULE"},
            {"name": "Ghost", "type": "UNKNOWN"},  # 非法类型，应丢弃
        ],
        "relations": [
            {"source": "Dify", "type": "CONTAINS", "target": "Core"},  # 合法
        ],
    }
    result = _filter_by_schema(payload, _schema())
    assert result.entities == [("Dify", "PRODUCT"), ("Core", "MODULE")]
    assert len(result.triples) == 1
    t = result.triples[0]
    assert (t.source, t.relation, t.target) == ("Dify", "CONTAINS", "Core")


def test_filter_drops_relation_to_missing_entity():
    payload = {
        "entities": [{"name": "Dify", "type": "PRODUCT"}],
        "relations": [
            {"source": "Dify", "type": "CONTAINS", "target": "NotExist"},  # 目标实体未抽取
        ],
    }
    result = _filter_by_schema(payload, _schema())
    assert result.entities == [("Dify", "PRODUCT")]
    assert result.triples == []


def test_filter_drops_triple_not_in_allowed_triples():
    # PRODUCT-CALLS-MODULE 不在 allowed_triples 内，应丢弃。
    payload = {
        "entities": [
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Core", "type": "MODULE"},
        ],
        "relations": [
            {"source": "Dify", "type": "CALLS", "target": "Core"},
        ],
    }
    result = _filter_by_schema(payload, _schema())
    assert result.triples == []


def test_filter_drops_blank_and_duplicate_entities():
    payload = {
        "entities": [
            {"name": "", "type": "PRODUCT"},  # 空名丢弃
            {"name": "Dify", "type": "PRODUCT"},
            {"name": "Dify", "type": "MODULE"},  # 重复名，保留首次类型
        ],
        "relations": [],
    }
    result = _filter_by_schema(payload, _schema())
    assert result.entities == [("Dify", "PRODUCT")]


def test_filter_handles_empty_and_malformed_payload():
    assert _filter_by_schema({}, _schema()).entities == []
    assert _filter_by_schema({"entities": "not-a-list"}, _schema()).triples == []
    assert _filter_by_schema({"entities": [], "relations": []}, _schema()).entities == []
    # 关系项非 dict 时不抛出
    result = _filter_by_schema({"entities": [], "relations": ["bad"]}, _schema())
    assert result.triples == []


def test_filter_dedups_triples_by_values():
    # 相同三元组重复出现应保留多次（MERGE 幂等），但实体去重。
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
    result = _filter_by_schema(payload, _schema())
    assert len(result.triples) == 2