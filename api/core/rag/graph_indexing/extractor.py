"""GraphRAG LLM 实体抽取器。

职责
====

- 接收单个段落文本与 Graph Schema，调用租户配置的 LLM 产出结构化 JSON。
- 用 ``json_repair`` 容错解析 LLM 输出。
- 按 ``GraphSchema.allowed_triples`` 过滤非法三元组，合法项才落库。

边界
====

- 不访问数据库，不写 Neo4j；输入由 ``indexer.py`` 提供。
- LLM 调用复用 ``core.model_manager.ModelManager``，与项目其它摘要/QA 生成
  保持一致的凭证获取链路，避免引入新的模型访问路径。
- 解析或调用失败抛出 ``GraphExtractionError``，由 ``indexer.py`` 决定重试或
  标记失败。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import json_repair

from core.model_manager import ModelManager
from core.rag.graph.entities import GraphSchema
from core.rag.graph_indexing.prompts import _SYSTEM_PROMPT, build_user_prompt
from graphon.model_runtime.entities.message_entities import (
    SystemPromptMessage,
    UserPromptMessage,
)
from graphon.model_runtime.entities.model_entities import ModelType

logger = logging.getLogger(__name__)


class GraphExtractionError(Exception):
    """LLM 实体抽取阶段的可预期错误，便于上层决定重试。"""


@dataclass
class ExtractedTriple:
    """单个已校验的三元组，供 Neo4j 写入层消费。"""

    source: str
    source_type: str
    relation: str
    target: str
    target_type: str


@dataclass
class ExtractionResult:
    """一次抽取的合法产物。"""

    entities: list[tuple[str, str]] = field(default_factory=list)
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
) -> ExtractionResult:
    """调用 LLM 抽取单个段落的实体与关系，并按 schema 过滤。

    :raises GraphExtractionError: LLM 调用或解析失败。
    """
    if not segment_text.strip():
        return ExtractionResult()

    prompt_messages = [
        SystemPromptMessage(content=_SYSTEM_PROMPT),
        UserPromptMessage(content=build_user_prompt(segment_text, schema)),
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

    # 当 stream=False 时返回 LLMResult，文本经 get_text_content 提取，兼容多模态。
    raw_text = getattr(getattr(response, "message", None), "get_text_content", lambda: "")()
    if not raw_text:
        raise GraphExtractionError("llm returned empty content")

    payload = json_repair.loads(raw_text)
    if not isinstance(payload, dict):
        raise GraphExtractionError("llm output is not a json object")

    return _filter_by_schema(payload, schema)


def _filter_by_schema(payload: dict, schema: GraphSchema) -> ExtractionResult:
    """按 GraphSchema 过滤非法实体与三元组。

    非法项被静默丢弃而非中断，保证部分可用结果可落库；这是与 Phase 3 容错
    策略一致的取舍（Q1=A）。
    """
    entity_type_set = set(schema.entity_types)
    relation_type_set = set(schema.relation_types)
    allowed_triples = {(s, r, t) for s, r, t in schema.allowed_triples}

    raw_entities = payload.get("entities") or []
    if not isinstance(raw_entities, list):
        raw_entities = []
    raw_relations = payload.get("relations") or []
    if not isinstance(raw_relations, list):
        raw_relations = []

    # name -> type 的合法实体映射，供三元组校验复用。
    name_to_type: dict[str, str] = {}
    entities: list[tuple[str, str]] = []
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        etype = str(item.get("type") or "").strip()
        if not name or etype not in entity_type_set:
            continue
        if name in name_to_type:
            continue
        name_to_type[name] = etype
        entities.append((name, etype))

    triples: list[ExtractedTriple] = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        rel = str(item.get("type") or "").strip()
        target = str(item.get("target") or "").strip()
        if not source or not rel or not target:
            continue
        source_type = name_to_type.get(source)
        target_type = name_to_type.get(target)
        # source/target 必须是已抽取的合法实体。
        if source_type is None or target_type is None:
            continue
        if rel not in relation_type_set:
            continue
        if (source_type, rel, target_type) not in allowed_triples:
            continue
        triples.append(
            ExtractedTriple(
                source=source,
                source_type=source_type,
                relation=rel,
                target=target,
                target_type=target_type,
            )
        )

    return ExtractionResult(entities=entities, triples=triples)


__all__ = [
    "ExtractedTriple",
    "ExtractionResult",
    "GraphExtractionError",
    "extract_with_llm",
]
