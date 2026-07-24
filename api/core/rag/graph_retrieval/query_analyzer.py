"""把用户问题转换为固定一跳 GraphQuery。

安全边界
========

- 模型只输出实体候选、关系类型和方向，禁止生成 Cypher。
- 输出必须再次按 Dataset GraphSchema 校验，最多保留 5 个实体。
- 模型失败时使用本地关键词回退；无法提取候选时返回 ``None``，由调用方继续
  基础检索。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

import json_repair

from core.model_manager import ModelManager
from core.rag.datasource.keyword.jieba.jieba_keyword_table_handler import JiebaKeywordTableHandler
from core.rag.graph.entities import (
    GraphExtractModelConfig,
    GraphQuery,
    GraphQueryDirection,
    GraphSchema,
)
from graphon.model_runtime.entities.message_entities import SystemPromptMessage, UserPromptMessage
from graphon.model_runtime.entities.model_entities import ModelType

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You extract graph search anchors from a user question.
Return one JSON object only:
{"entity_names":["..."],"relation_types":["..."],"direction":"both"}
Rules:
- entity_names: at most 5 literal names or technical terms from the question.
- relation_types: only values from the supplied whitelist.
- direction: outbound, inbound, or both.
- Never output Cypher, SQL, explanations, markdown, or credentials.
"""


def analyze_graph_query(
    *,
    tenant_id: str,
    query: str,
    model_config: GraphExtractModelConfig | None,
    schema: GraphSchema,
    limit: int,
) -> GraphQuery | None:
    """分析文本问题并返回安全 GraphQuery；无候选时返回 ``None``。"""
    normalized_query = query.strip()
    if not normalized_query:
        return None

    payload: Mapping[str, object] = {}
    if model_config is not None:
        try:
            payload = _invoke_query_model(
                tenant_id=tenant_id,
                query=normalized_query,
                model_config=model_config,
                schema=schema,
            )
        except Exception as ex:
            logger.warning(
                "graph query analyzer model failed tenant=%s model=%s err=%s",
                tenant_id,
                model_config.model,
                ex,
            )

    graph_query = _query_from_payload(payload, schema=schema, limit=limit)
    if graph_query is not None:
        return graph_query

    candidates = _extract_lexical_candidates(normalized_query)
    if not candidates:
        return None
    return GraphQuery(
        entity_names=candidates[:5],
        relation_types=[],
        direction=GraphQueryDirection.BOTH,
        max_depth=1,
        limit=max(1, min(int(limit), 20)),
    )


def _invoke_query_model(
    *,
    tenant_id: str,
    query: str,
    model_config: GraphExtractModelConfig | None,
    schema: GraphSchema,
) -> Mapping[str, object]:
    user_prompt = (
        f"Entity type whitelist: {schema.entity_types}\n"
        f"Relation type whitelist: {schema.relation_types}\n"
        f"Question: {query}"
    )
    model_instance = ModelManager.for_tenant(tenant_id=tenant_id).get_model_instance(
        tenant_id=tenant_id,
        provider=model_config.provider,
        model_type=ModelType.LLM,
        model=model_config.model,
    )
    parameters: dict[str, object] = {"temperature": 0}
    if model_config.max_tokens is not None:
        parameters["max_tokens"] = model_config.max_tokens
    response = model_instance.invoke_llm(
        prompt_messages=[SystemPromptMessage(content=_SYSTEM_PROMPT), UserPromptMessage(content=user_prompt)],
        model_parameters=parameters,
        stream=False,
    )
    raw_text = getattr(getattr(response, "message", None), "get_text_content", lambda: "")()
    if not raw_text:
        return {}
    parsed = json_repair.loads(raw_text)
    return parsed if isinstance(parsed, Mapping) else {}


def _query_from_payload(
    payload: Mapping[str, object],
    *,
    schema: GraphSchema,
    limit: int,
) -> GraphQuery | None:
    entity_names = _deduplicate_strings(payload.get("entity_names"), allowed=None, max_items=5)
    if not entity_names:
        return None
    relation_types = _deduplicate_strings(
        payload.get("relation_types"),
        allowed=set(schema.relation_types),
        max_items=len(schema.relation_types),
    )
    direction_value = str(payload.get("direction") or GraphQueryDirection.BOTH.value).strip().lower()
    try:
        direction = GraphQueryDirection(direction_value)
    except ValueError:
        direction = GraphQueryDirection.BOTH
    return GraphQuery(
        entity_names=entity_names,
        relation_types=relation_types,
        direction=direction,
        max_depth=1,
        limit=max(1, min(int(limit), 20)),
    )


def _deduplicate_strings(
    raw_values: object,
    *,
    allowed: set[str] | None,
    max_items: int,
) -> list[str]:
    if not isinstance(raw_values, list | tuple):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        if allowed is not None and value not in allowed:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= max_items:
            break
    return result


def _extract_lexical_candidates(query: str) -> list[str]:
    """使用引号短语和 Jieba 关键词作为模型失败时的无外部 I/O 回退。"""
    candidates: list[str] = []
    quoted = re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,64})[\"'“”‘’]", query)
    candidates.extend(item.strip() for item in quoted if item.strip())
    try:
        keywords = JiebaKeywordTableHandler().extract_keywords(query, 8)
        candidates.extend(str(item).strip() for item in keywords if str(item).strip())
    except Exception:
        logger.debug("graph lexical keyword extraction failed", exc_info=True)
        candidates.extend(re.findall(r"[A-Za-z0-9_.:/-]{2,64}|[\u4e00-\u9fff]{2,16}", query))
    return _deduplicate_strings(candidates, allowed=None, max_items=5)


__all__ = ["analyze_graph_query"]
