"""GraphRAG LLM 实体关系抽取器。

职责
====

- 调用租户配置的 LLM，为单个 Segment 生成结构化实体与关系 JSON。
- 用 ``json_repair`` 容错解析模型输出。
- 校验实体类型和关系类型；strict 模式额外执行 ``allowed_triples``。
- 对合法三元组去重，并在过滤后执行 ``max_triplets_per_chunk`` 上限。

边界
====

- 不访问数据库，不写 Neo4j；输入由 ``indexer.py`` 提供。
- LLM 调用复用 ``core.model_manager.ModelManager``，不引入新的凭据链路。
- 调用或解析失败抛出 ``GraphExtractionError``，由 Indexer 映射为重试结果。
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date

import json_repair

from core.model_manager import ModelManager
from core.rag.graph.entities import GraphPropertyDefinition, GraphSchema
from core.rag.graph_indexing.prompts import _SYSTEM_PROMPT, build_user_prompt
from graphon.model_runtime.entities.message_entities import (
    SystemPromptMessage,
    UserPromptMessage,
)
from graphon.model_runtime.entities.model_entities import ModelType

logger = logging.getLogger(__name__)


class GraphExtractionError(Exception):
    """LLM 实体抽取阶段的可预期错误。"""


@dataclass(frozen=True)
class ExtractedEntity:
    """单个已校验实体及其已声明的单值属性。"""

    name: str
    entity_type: str
    properties: dict[str, str | int | float | bool | date] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedTriple:
    """单个已校验三元组，供 Neo4j 写入层消费。"""

    source: str
    source_type: str
    relation: str
    target: str
    target_type: str


@dataclass
class ExtractionResult:
    """一次抽取中可安全写入图数据库的实体和事实。"""

    entities: list[ExtractedEntity] = field(default_factory=list)
    triples: list[ExtractedTriple] = field(default_factory=list)


def extract_with_llm(
    *,
    tenant_id: str,
    provider: str,
    model: str,
    temperature: float,
    max_tokens: int | None = None,
    segment_text: str,
    schema: GraphSchema,
    strict: bool = True,
    max_triplets_per_chunk: int = 10,
) -> ExtractionResult:
    """调用 LLM 抽取单个 Segment，并按配置过滤和限制结果。

    ``strict=True`` 时只保留 ``allowed_triples`` 中声明的组合；关闭 strict 后仍
    限制实体类型和关系类型，避免任意类型进入图数据库。

    :raises GraphExtractionError: LLM 调用或输出解析失败。
    """
    if not segment_text.strip():
        return ExtractionResult()

    prompt_messages = [
        SystemPromptMessage(content=_SYSTEM_PROMPT),
        UserPromptMessage(
            content=build_user_prompt(
                segment_text,
                schema,
                strict=strict,
                max_triplets_per_chunk=max_triplets_per_chunk,
            )
        ),
    ]
    model_parameters: dict[str, object] = {"temperature": temperature}
    if max_tokens is not None:
        model_parameters["max_tokens"] = max_tokens

    try:
        model_instance = ModelManager.for_tenant(tenant_id=tenant_id).get_model_instance(
            tenant_id=tenant_id,
            provider=provider,
            model_type=ModelType.LLM,
            model=model,
        )
        response = model_instance.invoke_llm(
            prompt_messages=prompt_messages,
            model_parameters=model_parameters,
            stream=False,
        )
    except Exception as ex:
        logger.warning("graph_extract llm invoke failed tenant=%s model=%s err=%s", tenant_id, model, ex)
        raise GraphExtractionError(f"llm invoke failed: {ex}") from ex

    payload = _parse_llm_response(response)

    return _filter_by_schema(
        payload,
        schema,
        strict=strict,
        max_triplets_per_chunk=max_triplets_per_chunk,
    )


def _parse_llm_response(response: object) -> dict:
    """从 LLM 响应中提取结构化 JSON 对象。

    部分带思考模式的模型会把最终结果放在 ``reasoning_content``、工具调用参数
    或结构化响应字段中，而不是 ``message.get_text_content()``。按优先级尝试这些
    字段，并且只有成功解析为对象时才接受，避免把纯思考文本当成抽取结果。
    """
    candidates = _iter_llm_response_candidates(response)
    has_candidate = False
    for raw_text in candidates:
        has_candidate = True
        try:
            return _parse_llm_payload(raw_text)
        except GraphExtractionError:
            continue

    if not has_candidate:
        raise GraphExtractionError("llm returned empty content")
    raise GraphExtractionError("llm output is not a json object")


def _iter_llm_response_candidates(response: object) -> Iterator[str]:
    """按响应字段优先级返回可能包含抽取 JSON 的文本候选。"""
    message = _get_response_field(response, "message")
    seen: set[str] = set()

    message_text = _get_response_field(message, "get_text_content")
    if callable(message_text):
        yield from _unique_text_candidates(message_text(), seen)

    yield from _unique_text_candidates(_get_response_field(message, "content"), seen)
    yield from _iter_tool_call_candidates(_get_response_field(message, "tool_calls"), seen)

    for field_name in ("structured_output", "output", "output_text", "text", "content"):
        yield from _unique_text_candidates(_get_response_field(response, field_name), seen)

    yield from _unique_text_candidates(_get_response_field(response, "reasoning_content"), seen)


def _iter_tool_call_candidates(tool_calls: object, seen: set[str]) -> Iterator[str]:
    """提取工具调用中的 function.arguments，兼容对象和字典响应。"""
    if not isinstance(tool_calls, (list, tuple)):
        return

    for tool_call in tool_calls:
        function = _get_response_field(tool_call, "function")
        arguments = _get_response_field(function, "arguments")
        yield from _unique_text_candidates(arguments, seen)


def _unique_text_candidates(value: object, seen: set[str]) -> Iterator[str]:
    """将响应字段转换为文本候选，并在同一响应内去重。"""
    for candidate in _text_candidates(value):
        normalized = candidate.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            yield normalized


def _text_candidates(value: object) -> Iterator[str]:
    """递归提取文本字段或结构化字典的 JSON 表示。"""
    if isinstance(value, str):
        yield value
        return

    if isinstance(value, Mapping):
        for field_name in ("text", "content", "data", "output_text", "arguments"):
            if field_name in value:
                yield from _text_candidates(value[field_name])
                return
        try:
            yield json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _text_candidates(item)


def _get_response_field(value: object, field_name: str) -> object:
    """读取对象或字典响应字段，忽略不存在的字段。"""
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _parse_llm_payload(raw_text: str) -> dict:
    """解析 LLM 输出，兼容被 JSON 字符串再次包裹的对象。"""
    payload = json_repair.loads(raw_text)
    if isinstance(payload, str):
        payload = json_repair.loads(payload)
    if not isinstance(payload, dict):
        raise GraphExtractionError("llm output is not a json object")
    return payload


def _filter_by_schema(
    payload: dict,
    schema: GraphSchema,
    *,
    strict: bool = True,
    max_triplets_per_chunk: int = 10,
) -> ExtractionResult:
    """过滤非法实体和关系，对三元组去重并执行数量上限。"""
    entity_type_set = set(schema.entity_types)
    relation_type_set = set(schema.relation_types)
    allowed_triples = set(schema.allowed_triples)

    raw_entities = payload.get("entities") or []
    if not isinstance(raw_entities, list):
        raw_entities = []
    raw_relations = payload.get("relations") or []
    if not isinstance(raw_relations, list):
        raw_relations = []

    name_to_type: dict[str, str] = {}
    entities: list[ExtractedEntity] = []
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        entity_type = str(item.get("type") or "").strip()
        if not name or entity_type not in entity_type_set or name in name_to_type:
            continue
        properties = _filter_entity_properties(
            item.get("properties"),
            schema.entity_properties.get(entity_type, []),
        )
        if properties is None:
            continue
        name_to_type[name] = entity_type
        entities.append(ExtractedEntity(name=name, entity_type=entity_type, properties=properties))

    triples: list[ExtractedTriple] = []
    seen_triples: set[tuple[str, str, str, str, str]] = set()
    limit = max(0, max_triplets_per_chunk)
    for item in raw_relations:
        if len(triples) >= limit:
            break
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        relation = str(item.get("type") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not relation or not target:
            continue
        source_type = name_to_type.get(source)
        target_type = name_to_type.get(target)
        if source_type is None or target_type is None or relation not in relation_type_set:
            continue
        if strict and (source_type, relation, target_type) not in allowed_triples:
            continue

        triple_key = (source, source_type, relation, target, target_type)
        if triple_key in seen_triples:
            continue
        seen_triples.add(triple_key)
        triples.append(
            ExtractedTriple(
                source=source,
                source_type=source_type,
                relation=relation,
                target=target,
                target_type=target_type,
            )
        )

    return ExtractionResult(entities=entities, triples=triples)


def _filter_entity_properties(
    raw_properties: object,
    definitions: list[GraphPropertyDefinition],
) -> dict[str, str | int | float | bool | date] | None:
    """按实体 Schema 验证嵌套属性，缺失必填属性时返回 ``None``。"""
    properties = raw_properties if isinstance(raw_properties, dict) else {}
    filtered: dict[str, str | int | float | bool | date] = {}
    for definition in definitions:
        value = _validate_property_value(properties.get(definition.name), definition)
        if value is None:
            if definition.required:
                return None
            continue
        filtered[definition.name] = value
    return filtered


def _validate_property_value(
    value: object,
    definition: GraphPropertyDefinition,
) -> str | int | float | bool | date | None:
    """严格验证一个 JSON 标量；不进行任何隐式类型转换。"""
    if definition.value_type == "string":
        return value if isinstance(value, str) and value.strip() else None
    if definition.value_type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            return None
        return value
    if definition.value_type == "boolean":
        return value if isinstance(value, bool) else None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "ExtractedEntity",
    "ExtractedTriple",
    "ExtractionResult",
    "GraphExtractionError",
    "extract_with_llm",
]
