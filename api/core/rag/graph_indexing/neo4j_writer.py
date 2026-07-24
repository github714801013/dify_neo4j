"""GraphRAG Neo4j 写入器。

职责
====

- 封装 Neo4j 官方 Python Driver，并按 ``dify_config.NEO4J_*`` 建立连接。
- 使用 ``GraphEntity``、``GraphFact``、``GraphSegment`` 三类节点保存实体、事实
  和 Segment 证据；同一事实可以由多个 Segment 独立举证。
- 所有图对象都带 tenant、Dataset、Document、节点 scope、graph_version、source_version
  隔离字段；每次 Segment 写入在单事务内替换该 Segment 的当前版本数据，避免
  LLM 重试结果变化时累积过期事实。
- 单个 Job 全部 Segment 写入成功后，由 ``finalize_document_version`` 删除同一
  文档的旧版本。写入或清理失败均抛出 ``GraphWriteError``。

边界
====

- 不负责 LLM 抽取；输入必须是已经过 Schema 校验和数量限制的
  ``ExtractionResult``。
- 不管理 Job 状态；``indexer.py`` 将异常映射为 retry/failed，Worker 再持久化。
- 旧版 ``Entity``/``Segment`` 标签不再写入，也不会被本模块自动删除；后续重建
  和生命周期任务负责显式清理遗留数据。
"""

from __future__ import annotations

import hashlib
import logging
from threading import Lock
from typing import TYPE_CHECKING, Protocol

from configs import dify_config
from core.rag.graph_indexing.entities import DATASET_GRAPH_INDEX_SCOPE
from core.rag.graph_indexing.extractor import ExtractedTriple, ExtractionResult

if TYPE_CHECKING:
    from neo4j import Driver

logger = logging.getLogger(__name__)


class GraphWriteError(Exception):
    """Neo4j 写入或版本切换阶段的可预期错误。"""


class _Neo4jTransaction(Protocol):
    def run(self, query: str, **parameters: object) -> object: ...


_driver: Driver | None = None
_schema_initialized = False
_schema_lock = Lock()

_SCHEMA_QUERIES = (
    "MATCH (n:GraphEntity) WHERE n.index_node_id IS NULL SET n.index_node_id = '__dataset__'",
    "MATCH (n:GraphFact) WHERE n.index_node_id IS NULL SET n.index_node_id = '__dataset__'",
    "MATCH (n:GraphSegment) WHERE n.index_node_id IS NULL SET n.index_node_id = '__dataset__'",
    "DROP CONSTRAINT graph_entity_identity IF EXISTS",
    "DROP CONSTRAINT graph_fact_identity IF EXISTS",
    "DROP CONSTRAINT graph_segment_identity IF EXISTS",
    "DROP INDEX graph_entity_lookup IF EXISTS",
    "DROP INDEX graph_fact_lookup IF EXISTS",
    "DROP INDEX graph_segment_lookup IF EXISTS",
    "CREATE CONSTRAINT graph_entity_identity IF NOT EXISTS "
    "FOR (n:GraphEntity) REQUIRE "
    "(n.tenant_id, n.dataset_id, n.document_id, n.index_node_id, "
    "n.graph_version, n.source_version, n.name, n.type) IS UNIQUE",
    "CREATE CONSTRAINT graph_fact_identity IF NOT EXISTS "
    "FOR (n:GraphFact) REQUIRE "
    "(n.tenant_id, n.dataset_id, n.document_id, n.index_node_id, "
    "n.graph_version, n.source_version, n.fact_key) IS UNIQUE",
    "CREATE CONSTRAINT graph_segment_identity IF NOT EXISTS "
    "FOR (n:GraphSegment) REQUIRE "
    "(n.tenant_id, n.dataset_id, n.document_id, n.index_node_id, n.graph_version, n.source_version, n.id) IS UNIQUE",
    "CREATE INDEX graph_entity_lookup IF NOT EXISTS "
    "FOR (n:GraphEntity) ON (n.tenant_id, n.dataset_id, n.index_node_id, n.graph_version, n.source_version, n.name)",
    "CREATE INDEX graph_fact_lookup IF NOT EXISTS "
    "FOR (n:GraphFact) ON (n.tenant_id, n.dataset_id, n.index_node_id, n.graph_version, n.source_version, n.relation)",
    "CREATE INDEX graph_segment_lookup IF NOT EXISTS "
    "FOR (n:GraphSegment) ON "
    "(n.tenant_id, n.dataset_id, n.document_id, n.index_node_id, "
    "n.graph_version, n.source_version)",
)


def _get_driver() -> Driver:
    """惰性创建并复用进程级 Neo4j Driver 单例。"""
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


def get_graph_driver() -> Driver:
    """返回进程级 Neo4j Driver，供只读 Graph Retrieval 复用连接池。"""
    return _get_driver()


def get_graph_database_name() -> str | None:
    """返回配置的 Neo4j 数据库名称；为空时使用驱动默认库。"""
    return getattr(dify_config, "NEO4J_DATABASE", None) or None


def _database_name() -> str | None:
    return get_graph_database_name()


def ensure_graph_schema() -> None:
    """幂等创建 GraphRAG 节点约束与高频查询索引。

    每个 Worker 进程最多执行一次；多进程并发依赖 Neo4j ``IF NOT EXISTS``
    保证安全。初始化失败时不缓存成功状态，后续 Job 可以重试。
    """
    global _schema_initialized
    if _schema_initialized:
        return

    with _schema_lock:
        if _schema_initialized:
            return
        driver = _get_driver()
        try:
            with driver.session(database=_database_name()) as session:
                for query in _SCHEMA_QUERIES:
                    result = session.run(query)
                    consume = getattr(result, "consume", None)
                    if callable(consume):
                        consume()
        except Exception as ex:
            raise GraphWriteError(f"neo4j schema initialization failed: {ex}") from ex
        _schema_initialized = True


def write_segment(
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    segment_id: str,
    graph_version: str,
    source_version: str,
    extraction: ExtractionResult,
    index_node_id: str = DATASET_GRAPH_INDEX_SCOPE,
) -> int:
    """把单个 Segment 的实体、事实和证据幂等写入 Neo4j。

    同一事实由多个 Segment 支撑时，共享 ``GraphFact`` 节点，每个 Segment 分别
    建立 ``EVIDENCE_FOR`` 关系。相同 Segment 重复执行不会生成重复图对象。

    :return: 本次输入中的事实数量，用于日志观测。
    :raises GraphWriteError: Neo4j 写入失败。
    """
    driver = _get_driver()
    try:
        with driver.session(database=_database_name()) as session:
            session.execute_write(
                _write_segment_tx,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                segment_id=segment_id,
                graph_version=graph_version,
                source_version=source_version,
                extraction=extraction,
                index_node_id=index_node_id,
            )
    except GraphWriteError:
        raise
    except Exception as ex:
        raise GraphWriteError(f"neo4j write failed: {ex}") from ex
    return len(extraction.triples)


def discard_document_version(
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    graph_version: str,
    source_version: str,
    index_node_id: str = DATASET_GRAPH_INDEX_SCOPE,
) -> None:
    """删除过期 Job 为指定文档版本写入的全部临时图对象。"""
    driver = _get_driver()
    try:
        with driver.session(database=_database_name()) as session:
            session.execute_write(
                _discard_document_version_tx,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                graph_version=graph_version,
                source_version=source_version,
                index_node_id=index_node_id,
            )
    except GraphWriteError:
        raise
    except Exception as ex:
        raise GraphWriteError(f"neo4j version discard failed: {ex}") from ex


def reconcile_dataset_graph(
    *,
    tenant_id: str,
    dataset_id: str,
    active_document_ids: tuple[str, ...],
    active_segment_ids: tuple[str, ...],
    index_node_id: str = DATASET_GRAPH_INDEX_SCOPE,
) -> None:
    """清理 Dataset 中已删除、归档、禁用或失效的图对象。

    对账只删除不再有效的 Document/Segment；活动文档的旧 source_version 继续
    保留到新版本成功，由 ``finalize_document_version`` 原子替换。
    """
    driver = _get_driver()
    try:
        with driver.session(database=_database_name()) as session:
            session.execute_write(
                _reconcile_dataset_graph_tx,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                active_document_ids=list(active_document_ids),
                active_segment_ids=list(active_segment_ids),
                index_node_id=index_node_id,
            )
    except GraphWriteError:
        raise
    except Exception as ex:
        raise GraphWriteError(f"neo4j dataset reconcile failed: {ex}") from ex


def finalize_document_version(
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    graph_version: str,
    source_version: str,
    index_node_id: str = DATASET_GRAPH_INDEX_SCOPE,
) -> None:
    """在新版本全部写入成功后删除同文档的旧 GraphRAG 版本。

    清理只触及 ``GraphEntity``、``GraphFact``、``GraphSegment`` 三类本地标签；
    当前版本节点始终保留。该操作是 Job 成功前的最后一步，失败时旧版本仍在。
    """
    driver = _get_driver()
    try:
        with driver.session(database=_database_name()) as session:
            session.execute_write(
                _finalize_document_version_tx,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                graph_version=graph_version,
                source_version=source_version,
                index_node_id=index_node_id,
            )
    except GraphWriteError:
        raise
    except Exception as ex:
        raise GraphWriteError(f"neo4j version cleanup failed: {ex}") from ex


def _write_segment_tx(
    tx: _Neo4jTransaction,
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    segment_id: str,
    graph_version: str,
    source_version: str,
    extraction: ExtractionResult,
    index_node_id: str = DATASET_GRAPH_INDEX_SCOPE,
) -> None:
    scope = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "segment_id": segment_id,
        "graph_version": graph_version,
        "source_version": source_version,
        "index_node_id": index_node_id,
    }
    tx.run(
        "MATCH (existing:GraphSegment {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, "
            "source_version: $source_version, id: $segment_id}) "
        "DETACH DELETE existing",
        **scope,
    )
    tx.run(
        "MERGE (s:GraphSegment {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, "
            "source_version: $source_version, id: $segment_id}) "
        "ON CREATE SET s.created_at = datetime() "
        "SET s.updated_at = datetime()",
        **scope,
    )

    for entity in extraction.entities:
        entity_attributes = {f"attr_{name}": value for name, value in entity.properties.items()}
        tx.run(
            "MATCH (s:GraphSegment {"
            "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
            "index_node_id: $index_node_id, graph_version: $graph_version, "
            "source_version: $source_version, id: $segment_id}) "
            "MERGE (e:GraphEntity {"
            "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
            "index_node_id: $index_node_id, graph_version: $graph_version, source_version: $source_version, "
            "name: $entity_name, type: $entity_type}) "
            "ON CREATE SET e.created_at = datetime() "
            "SET e.updated_at = datetime(), e += $entity_attributes "
            "MERGE (s)-[mention:MENTIONS]->(e) "
            "SET mention.tenant_id = $tenant_id, mention.dataset_id = $dataset_id, "
            "mention.document_id = $document_id, mention.index_node_id = $index_node_id, "
            "mention.graph_version = $graph_version, mention.source_version = $source_version",
            **scope,
            entity_name=entity.name,
            entity_type=entity.entity_type,
            entity_attributes=entity_attributes,
        )

    for triple in extraction.triples:
        _upsert_fact(tx, scope=scope, triple=triple)

    _delete_orphan_current_version_nodes(tx, scope=scope)


def _upsert_fact(
    tx: _Neo4jTransaction,
    *,
    scope: dict[str, str],
    triple: ExtractedTriple,
) -> None:
    fact_key = _build_fact_key(triple)
    tx.run(
        "MATCH (s:GraphSegment {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, "
            "source_version: $source_version, id: $segment_id}) "
        "MATCH (source:GraphEntity {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, source_version: $source_version, "
        "name: $source_name, type: $source_type}) "
        "MATCH (target:GraphEntity {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, source_version: $source_version, "
        "name: $target_name, type: $target_type}) "
        "MERGE (f:GraphFact {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, "
        "source_version: $source_version, fact_key: $fact_key}) "
        "ON CREATE SET f.created_at = datetime() "
        "SET f.updated_at = datetime(), f.relation = $relation, "
        "f.source_name = $source_name, f.source_type = $source_type, "
        "f.target_name = $target_name, f.target_type = $target_type "
        "MERGE (source)-[source_link:FACT_SOURCE]->(f) "
        "SET source_link.tenant_id = $tenant_id, source_link.dataset_id = $dataset_id, "
        "source_link.document_id = $document_id, source_link.index_node_id = $index_node_id, "
        "source_link.graph_version = $graph_version, source_link.source_version = $source_version "
        "MERGE (f)-[target_link:FACT_TARGET]->(target) "
        "SET target_link.tenant_id = $tenant_id, target_link.dataset_id = $dataset_id, "
        "target_link.document_id = $document_id, target_link.index_node_id = $index_node_id, "
        "target_link.graph_version = $graph_version, target_link.source_version = $source_version "
        "MERGE (s)-[evidence:EVIDENCE_FOR]->(f) "
        "SET evidence.tenant_id = $tenant_id, evidence.dataset_id = $dataset_id, "
        "evidence.document_id = $document_id, evidence.index_node_id = $index_node_id, "
        "evidence.graph_version = $graph_version, evidence.source_version = $source_version",
        **scope,
        fact_key=fact_key,
        source_name=triple.source,
        source_type=triple.source_type,
        relation=triple.relation,
        target_name=triple.target,
        target_type=triple.target_type,
    )


def _delete_orphan_current_version_nodes(tx: _Neo4jTransaction, *, scope: dict[str, str]) -> None:
    """删除当前版本中已无 Segment 证据的事实和实体。"""
    tx.run(
        "MATCH (fact:GraphFact {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, source_version: $source_version}) "
        "WHERE NOT EXISTS { MATCH (:GraphSegment {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "document_id: $document_id, index_node_id: $index_node_id, graph_version: $graph_version, "
        "source_version: $source_version})-[:EVIDENCE_FOR {index_node_id: $index_node_id}]->(fact) } "
        "DETACH DELETE fact",
        **scope,
    )
    tx.run(
        "MATCH (entity:GraphEntity {"
        "tenant_id: $tenant_id, dataset_id: $dataset_id, document_id: $document_id, "
        "index_node_id: $index_node_id, graph_version: $graph_version, source_version: $source_version}) "
        "WHERE NOT EXISTS { MATCH (:GraphSegment {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "document_id: $document_id, index_node_id: $index_node_id, graph_version: $graph_version, "
        "source_version: $source_version})-[:MENTIONS {index_node_id: $index_node_id}]->(entity) } "
        "AND NOT EXISTS { MATCH (entity)-[:FACT_SOURCE {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}]->(:GraphFact {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}) } "
        "AND NOT EXISTS { MATCH (:GraphFact {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id})-[:FACT_TARGET {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}]->(entity) } "
        "DETACH DELETE entity",
        **scope,
    )


def _reconcile_dataset_graph_tx(
    tx: _Neo4jTransaction,
    *,
    tenant_id: str,
    dataset_id: str,
    active_document_ids: list[str],
    active_segment_ids: list[str],
    index_node_id: str,
) -> None:
    parameters = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "index_node_id": index_node_id,
        "active_document_ids": active_document_ids,
        "active_segment_ids": active_segment_ids,
    }
    tx.run(
        "MATCH (node:GraphSegment) "
        "WHERE node.tenant_id = $tenant_id AND node.dataset_id = $dataset_id "
        "AND node.index_node_id = $index_node_id "
        "AND (NOT node.document_id IN $active_document_ids OR NOT node.id IN $active_segment_ids) "
        "DETACH DELETE node",
        **parameters,
    )
    for label in ("GraphFact", "GraphEntity"):
        tx.run(
            f"MATCH (node:{label}) "
            "WHERE node.tenant_id = $tenant_id AND node.dataset_id = $dataset_id "
            "AND node.index_node_id = $index_node_id "
            "AND NOT node.document_id IN $active_document_ids "
            "DETACH DELETE node",
            **parameters,
        )
    tx.run(
        "MATCH (fact:GraphFact) "
        "WHERE fact.tenant_id = $tenant_id AND fact.dataset_id = $dataset_id "
        "AND fact.index_node_id = $index_node_id "
        "AND NOT EXISTS { MATCH (:GraphSegment {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id})-[:EVIDENCE_FOR {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}]->(fact) } "
        "DETACH DELETE fact",
        **parameters,
    )
    tx.run(
        "MATCH (entity:GraphEntity) "
        "WHERE entity.tenant_id = $tenant_id AND entity.dataset_id = $dataset_id "
        "AND entity.index_node_id = $index_node_id "
        "AND NOT EXISTS { MATCH (:GraphSegment {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id})-[:MENTIONS {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}]->(entity) } "
        "AND NOT EXISTS { MATCH (entity)-[:FACT_SOURCE {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}]->(:GraphFact {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}) } "
        "AND NOT EXISTS { MATCH (:GraphFact {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id})-[:FACT_TARGET {tenant_id: $tenant_id, dataset_id: $dataset_id, "
        "index_node_id: $index_node_id}]->(entity) } "
        "DETACH DELETE entity",
        **parameters,
    )


def _discard_document_version_tx(
    tx: _Neo4jTransaction,
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    graph_version: str,
    source_version: str,
    index_node_id: str,
) -> None:
    parameters = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "graph_version": graph_version,
        "source_version": source_version,
        "index_node_id": index_node_id,
    }
    for label in ("GraphSegment", "GraphFact", "GraphEntity"):
        tx.run(
            f"MATCH (node:{label}) "
            "WHERE node.tenant_id = $tenant_id "
            "AND node.dataset_id = $dataset_id "
            "AND node.document_id = $document_id "
            "AND node.index_node_id = $index_node_id "
            "AND node.graph_version = $graph_version "
            "AND node.source_version = $source_version "
            "DETACH DELETE node",
            **parameters,
        )


def _finalize_document_version_tx(
    tx: _Neo4jTransaction,
    *,
    tenant_id: str,
    dataset_id: str,
    document_id: str,
    graph_version: str,
    source_version: str,
    index_node_id: str,
) -> None:
    parameters = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "graph_version": graph_version,
        "source_version": source_version,
        "index_node_id": index_node_id,
    }
    # 按标签执行查询，允许 Neo4j 使用各标签的范围索引，避免 MATCH (node) 全图扫描。
    for label in ("GraphSegment", "GraphFact", "GraphEntity"):
        tx.run(
            f"MATCH (node:{label}) "
            "WHERE node.tenant_id = $tenant_id "
            "AND node.dataset_id = $dataset_id "
            "AND node.document_id = $document_id "
            "AND node.index_node_id = $index_node_id "
            "AND (node.graph_version <> $graph_version OR node.source_version <> $source_version) "
            "DETACH DELETE node",
            **parameters,
        )


def _build_fact_key(triple: ExtractedTriple) -> str:
    payload = "\x1f".join(
        (
            triple.source_type,
            triple.source,
            triple.relation,
            triple.target_type,
            triple.target,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "GraphWriteError",
    "discard_document_version",
    "ensure_graph_schema",
    "finalize_document_version",
    "get_graph_database_name",
    "get_graph_driver",
    "reconcile_dataset_graph",
    "write_segment",
]
