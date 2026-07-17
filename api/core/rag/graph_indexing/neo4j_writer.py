"""GraphRAG Neo4j 写入器。

职责
====

- 封装 neo4j 官方 Python 驱动的会话管理，按 ``dify_config.NEO4J_*`` 连接。
- 对单个 segment 的实体与关系做幂等 upsert，节点/边均附带
  ``graph_version``、``segment_id``、``dataset_id``，便于后续按版本清理。
- 所有写操作使用参数化 Cypher，避免注入；失败抛出 ``GraphWriteError``。

边界
====

- 不负责 LLM 抽取；输入为已校验的 ``ExtractionResult``。
- 不管理 Job 状态；成功/失败由 ``indexer.py`` 通过 repository 标记。
- 驱动单例按进程持有，复用连接池；Worker 进程内多次调用共享同一 driver。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from configs import dify_config
from core.rag.graph_indexing.extractor import ExtractionResult

if TYPE_CHECKING:
    from neo4j import Driver

logger = logging.getLogger(__name__)


class GraphWriteError(Exception):
    """Neo4j 写入阶段的可预期错误，便于上层决定重试。"""


_driver: Driver | None = None


def _get_driver() -> Driver:
    """惰性创建并复用进程级 neo4j driver 单例。

    连接配置缺失时抛出 GraphWriteError，由调用方标记 Job 失败，避免每次
    抽取都重复校验配置。
    """
    global _driver
    if _driver is not None:
        return _driver

    uri = getattr(dify_config, "NEO4J_URI", None)
    username = getattr(dify_config, "NEO4J_USERNAME", None)
    password = getattr(dify_config, "NEO4J_PASSWORD", None)
    if not uri or not username or not password:
        raise GraphWriteError("neo4j connection config is missing")

    try:
        from neo4j import GraphDatabase

        _driver = GraphDatabase.driver(uri, auth=(username, password))
    except Exception as ex:
        raise GraphWriteError(f"failed to create neo4j driver: {ex}") from ex
    return _driver


def write_segment(
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    segment_id: str,
    graph_version: str,
    extraction: ExtractionResult,
) -> int:
    """把单个 segment 的抽取结果幂等写入 Neo4j。

    :return: 写入的关系数量，用于日志观测。
    :raises GraphWriteError: 写入失败。
    """
    if not extraction.triples:
        # 无三元组时仍写入实体节点，保证实体可被检索；关系数为 0。
        _upsert_entities(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            segment_id=segment_id,
            graph_version=graph_version,
            extraction=extraction,
        )
        return 0

    driver = _get_driver()
    database = getattr(dify_config, "NEO4J_DATABASE", None) or None
    try:
        with driver.session(database=database) as session:
            _upsert_entities(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                segment_id=segment_id,
                graph_version=graph_version,
                extraction=extraction,
                session=session,
            )
            for triple in extraction.triples:
                session.execute_write(
                    _upsert_relation_tx,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                    segment_id=segment_id,
                    graph_version=graph_version,
                    source=triple.source,
                    source_type=triple.source_type,
                    relation=triple.relation,
                    target=triple.target,
                    target_type=triple.target_type,
                )
    except GraphWriteError:
        raise
    except Exception as ex:
        raise GraphWriteError(f"neo4j write failed: {ex}") from ex
    return len(extraction.triples)


def _upsert_entities(
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    segment_id: str,
    graph_version: str,
    extraction: ExtractionResult,
    session=None,
) -> None:
    """写入实体节点。session 为空时自建临时会话。"""
    if not extraction.entities:
        return

    def _tx(tx):
        for name, etype in extraction.entities:
            tx.run(
                # 节点标签使用稳定的 Entity，类型用属性承载，避免动态标签带来
                # 升级与查询兼容问题。
                "MERGE (n:Entity {name: $name, dataset_id: $dataset_id}) "
                "ON CREATE SET n.type = $type, n.tenant_id = $tenant_id, "
                "n.created_at = datetime(), n.graph_version = $graph_version "
                "ON MATCH SET n.type = $type, n.graph_version = $graph_version",
                name=name,
                dataset_id=dataset_id,
                type=etype,
                tenant_id=tenant_id,
                graph_version=graph_version,
            )
            tx.run(
                "MATCH (n:Entity {name: $name, dataset_id: $dataset_id}) "
                "MERGE (s:Segment {id: $segment_id}) "
                "MERGE (s)-[:MENTIONS]->(n)",
                name=name,
                dataset_id=dataset_id,
                segment_id=segment_id,
            )

    if session is not None:
        session.execute_write(_tx)
    else:
        driver = _get_driver()
        database = getattr(dify_config, "NEO4J_DATABASE", None) or None
        with driver.session(database=database) as s:
            s.execute_write(_tx)


def _upsert_relation_tx(
    tx,
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    segment_id: str,
    graph_version: str,
    source: str,
    source_type: str,
    relation: str,
    target: str,
    target_type: str,
) -> None:
    """在事务函数内写入一条关系。

    关系类型在 Cypher 中不能用参数化（Neo4j 限制），但 relation 来自
    GraphSchema.relation_types 白名单，不是 LLM 任意输出，可安全拼接到语句。
    """
    # 关系类型按 Neo4j 规范使用全大写且不含空格的白名单值。
    safe_relation = relation.upper()
    tx.run(
        "MERGE (a:Entity {name: $source, dataset_id: $dataset_id}) "
        "SET a.type = $source_type, a.graph_version = $graph_version "
        "MERGE (b:Entity {name: $target, dataset_id: $dataset_id}) "
        "SET b.type = $target_type, b.graph_version = $graph_version "
        f"MERGE (a)-[r:{safe_relation}]->(b) "
        "SET r.graph_version = $graph_version, r.segment_id = $segment_id, "
        "r.dataset_id = $dataset_id, r.tenant_id = $tenant_id, "
        "r.document_id = $document_id",
        source=source,
        target=target,
        dataset_id=dataset_id,
        source_type=source_type,
        target_type=target_type,
        graph_version=graph_version,
        segment_id=segment_id,
        tenant_id=tenant_id,
        document_id=document_id,
    )


__all__ = ["GraphWriteError", "write_segment"]