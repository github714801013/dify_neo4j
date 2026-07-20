"""安全的一跳 Neo4j GraphRAG Reader。

只允许固定模板和参数化值，不接收或执行模型生成的 Cypher。查询同时限定
Tenant、Dataset、graph_version，并只读取数据库层确认仍为当前版本的文档图。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.rag.graph.entities import GraphQuery, GraphResult
from core.rag.graph_indexing.neo4j_writer import get_graph_database_name, get_graph_driver


class GraphReadError(Exception):
    """Neo4j 图读取失败。"""


@dataclass(frozen=True)
class ActiveGraphVersion:
    """当前 Document 已成功且仍与 Dify 内容一致的图版本。"""

    document_id: str
    source_version: str


_ONE_HOP_QUERY = """
MATCH (source:GraphEntity)-[:FACT_SOURCE]->(fact:GraphFact)-[:FACT_TARGET]->(target:GraphEntity)
MATCH (segment:GraphSegment)-[:EVIDENCE_FOR]->(fact)
WHERE segment.tenant_id = $tenant_id
  AND segment.dataset_id = $dataset_id
  AND segment.graph_version = $graph_version
  AND fact.tenant_id = $tenant_id
  AND fact.dataset_id = $dataset_id
  AND fact.graph_version = $graph_version
  AND source.tenant_id = $tenant_id
  AND source.dataset_id = $dataset_id
  AND source.graph_version = $graph_version
  AND target.tenant_id = $tenant_id
  AND target.dataset_id = $dataset_id
  AND target.graph_version = $graph_version
  AND fact.document_id = segment.document_id
  AND fact.source_version = segment.source_version
  AND source.document_id = segment.document_id
  AND source.source_version = segment.source_version
  AND target.document_id = segment.document_id
  AND target.source_version = segment.source_version
  AND any(version IN $active_versions
          WHERE version.document_id = segment.document_id
            AND version.source_version = segment.source_version)
  AND (size($relation_types) = 0 OR fact.relation IN $relation_types)
  AND (
       ($direction = 'both' AND (
          any(name IN $entity_names WHERE toLower(source.name) CONTAINS name OR name CONTAINS toLower(source.name))
          OR any(name IN $entity_names WHERE toLower(target.name) CONTAINS name OR name CONTAINS toLower(target.name))
       ))
       OR ($direction = 'outbound' AND
          any(name IN $entity_names WHERE toLower(source.name) CONTAINS name OR name CONTAINS toLower(source.name)))
       OR ($direction = 'inbound' AND
          any(name IN $entity_names WHERE toLower(target.name) CONTAINS name OR name CONTAINS toLower(target.name)))
  )
WITH segment, source, fact, target,
     CASE
       WHEN any(name IN $entity_names WHERE toLower(source.name) CONTAINS name OR name CONTAINS toLower(source.name))
       THEN source.name
       ELSE target.name
     END AS matched_entity
RETURN segment.id AS segment_id,
       1 AS graph_distance,
       collect(DISTINCT source.name + ' -[' + fact.relation + ']-> ' + target.name)[0..5] AS graph_path,
       collect(DISTINCT matched_entity)[0..5] AS matched_entities,
       collect(DISTINCT fact.relation)[0..5] AS relation_types,
       count(DISTINCT fact) AS evidence_count
ORDER BY evidence_count DESC, segment.id
LIMIT $limit
""".strip()


def read_graph_results(
    *,
    tenant_id: str,
    dataset_id: str,
    graph_version: str,
    active_versions: tuple[ActiveGraphVersion, ...],
    graph_query: GraphQuery,
    timeout_ms: int,
) -> list[GraphResult]:
    """执行固定一跳图查询并映射为按排名排序的 GraphResult。"""
    if not active_versions:
        return []

    parameters: dict[str, object] = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "graph_version": graph_version,
        "active_versions": [
            {"document_id": item.document_id, "source_version": item.source_version} for item in active_versions
        ],
        "entity_names": [item.casefold() for item in graph_query.entity_names],
        "relation_types": graph_query.relation_types,
        "direction": graph_query.direction.value,
        "limit": graph_query.limit,
    }
    try:
        driver = get_graph_driver()
        with driver.session(database=get_graph_database_name()) as session:
            result = session.run(_query_with_timeout(_ONE_HOP_QUERY, timeout_ms), **parameters)
            rows = list(result)
    except Exception as ex:
        raise GraphReadError(f"neo4j graph query failed: {ex}") from ex

    graph_results: list[GraphResult] = []
    for rank, row in enumerate(rows, start=1):
        segment_id = str(_record_value(row, "segment_id") or "").strip()
        if not segment_id:
            continue
        graph_results.append(
            GraphResult(
                segment_id=segment_id,
                graph_rank=rank,
                graph_distance=int(_record_value(row, "graph_distance") or 1),
                graph_path=_string_list(_record_value(row, "graph_path")),
                matched_entities=_string_list(_record_value(row, "matched_entities")),
                relation_types=_string_list(_record_value(row, "relation_types")),
            )
        )
    return graph_results


def _query_with_timeout(query_text: str, timeout_ms: int):
    from neo4j import Query

    return Query(query_text, timeout=max(0.1, timeout_ms / 1000))


def _record_value(record: object, key: str) -> object:
    getter = getattr(record, "get", None)
    if callable(getter):
        return getter(key)
    if isinstance(record, dict):
        return record.get(key)
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item).strip()]


__all__ = [
    "ActiveGraphVersion",
    "GraphReadError",
    "read_graph_results",
]
