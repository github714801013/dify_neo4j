"""已发布 Pipeline 的节点图谱 scope 解析。

节点图谱配置只能从 ``Pipeline.workflow_id`` 指向的已发布 Workflow 读取。
Draft 仅用于编辑，不得驱动历史 Document 的异步重建或 Worker 写入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.workflow.nodes.knowledge_index import KNOWLEDGE_INDEX_NODE_TYPE
from core.workflow.nodes.knowledge_index.entities import GraphIndexConfig
from models.dataset import Dataset, Pipeline
from models.workflow import Workflow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublishedNodeGraphScope:
    """一个已发布且启用的 Knowledge Base 节点图谱配置。"""

    tenant_id: str
    dataset_id: str
    index_node_id: str
    graph_version: str
    graph_config: GraphIndexConfig


def list_published_node_graph_scopes(
    session: Session,
    *,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
) -> list[PublishedNodeGraphScope]:
    """解析所有已发布 Pipeline 中启用且有效的节点图谱配置。"""
    stmt = (
        select(Dataset, Workflow)
        .join(Pipeline, (Pipeline.id == Dataset.pipeline_id) & (Pipeline.tenant_id == Dataset.tenant_id))
        .join(Workflow, (Workflow.id == Pipeline.workflow_id) & (Workflow.tenant_id == Dataset.tenant_id))
        .where(Pipeline.workflow_id.is_not(None))
    )
    if tenant_id is not None:
        stmt = stmt.where(Dataset.tenant_id == tenant_id)
    if dataset_id is not None:
        stmt = stmt.where(Dataset.id == dataset_id)

    scopes: list[PublishedNodeGraphScope] = []
    for dataset, workflow in session.execute(stmt):
        nodes = workflow.graph_dict.get("nodes", [])
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            data = node.get("data")
            if not isinstance(node_id, str) or not isinstance(data, dict):
                continue
            if data.get("type") != KNOWLEDGE_INDEX_NODE_TYPE:
                continue
            raw_config = data.get("graph_index_config")
            if raw_config is None:
                continue
            try:
                graph_config = GraphIndexConfig.model_validate(raw_config)
            except ValueError:
                logger.warning(
                    "ignore invalid published graph_index_config tenant=%s dataset=%s node=%s",
                    dataset.tenant_id,
                    dataset.id,
                    node_id,
                )
                continue
            if not graph_config.enabled or graph_config.graph_version is None:
                continue
            scopes.append(
                PublishedNodeGraphScope(
                    tenant_id=dataset.tenant_id,
                    dataset_id=dataset.id,
                    index_node_id=node_id,
                    graph_version=graph_config.graph_version,
                    graph_config=graph_config,
                )
            )
    return scopes


def resolve_published_node_graph_scope(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    index_node_id: str,
) -> PublishedNodeGraphScope | None:
    """返回指定节点当前已发布的启用图谱配置。"""
    return next(
        (
            scope
            for scope in list_published_node_graph_scopes(session, tenant_id=tenant_id, dataset_id=dataset_id)
            if scope.index_node_id == index_node_id
        ),
        None,
    )
