"""GraphRAG 领域边界。

本包只包含 GraphRAG 专用契约，刻意不导入索引、检索、LlamaIndex 或 Neo4j
实现，确保关闭功能的部署继续走既有 Dify 执行链路。
"""

from .entities import (
    DEFAULT_GRAPH_SCHEMA,
    GraphExtractModelConfig,
    GraphQuery,
    GraphQueryDirection,
    GraphQueryMode,
    GraphRagConfigInput,
    GraphResult,
    GraphSchema,
    KnowledgeCandidate,
)

__all__ = [
    "DEFAULT_GRAPH_SCHEMA",
    "GraphExtractModelConfig",
    "GraphQuery",
    "GraphQueryDirection",
    "GraphQueryMode",
    "GraphRagConfigInput",
    "GraphResult",
    "GraphSchema",
    "KnowledgeCandidate",
]
