from collections.abc import Mapping
from typing import Any, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from core.rag.entities import RerankingModelConfig, WeightedScoreConfig
from core.rag.graph.entities import GraphExtractModelConfig, GraphPropertyDefinition
from core.rag.graph_indexing.versioning import compute_graph_version
from core.rag.index_processor.index_processor_base import SummaryIndexSettingDict
from core.rag.retrieval.retrieval_methods import RetrievalMethod
from core.workflow.nodes.knowledge_index import KNOWLEDGE_INDEX_NODE_TYPE
from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType


class RetrievalSetting(BaseModel):
    """
    Retrieval Setting.
    """

    search_method: RetrievalMethod
    top_k: int
    score_threshold: float | None = 0.5
    score_threshold_enabled: bool = False
    reranking_mode: str = "reranking_model"
    reranking_enable: bool = True
    reranking_model: RerankingModelConfig | None = None
    weights: WeightedScoreConfig | None = None


class FileInfo(BaseModel):
    """
    File Info.
    """

    file_id: str


class OnlineDocumentIcon(BaseModel):
    """
    Document Icon.
    """

    icon_url: str
    icon_type: str
    icon_emoji: str


class OnlineDocumentInfo(BaseModel):
    """
    Online document info.
    """

    provider: str
    workspace_id: str | None = None
    page_id: str
    page_type: str
    icon: OnlineDocumentIcon | None = None


class WebsiteInfo(BaseModel):
    """
    website import info.
    """

    provider: str
    url: str


class GeneralStructureChunk(BaseModel):
    """
    General Structure Chunk.
    """

    general_chunks: list[str]
    data_source_info: Union[FileInfo, OnlineDocumentInfo, WebsiteInfo]


class ParentChildChunk(BaseModel):
    """
    Parent Child Chunk.
    """

    parent_content: str
    child_contents: list[str]


class ParentChildStructureChunk(BaseModel):
    """
    Parent Child Structure Chunk.
    """

    parent_child_chunks: list[ParentChildChunk]
    data_source_info: Union[FileInfo, OnlineDocumentInfo, WebsiteInfo]


class GraphSchemaType(BaseModel):
    """图谱 Schema 的实体或关系类型及其可抽取属性。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    properties: list[GraphPropertyDefinition] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("graph schema type names cannot be blank")
        return normalized_value

    @model_validator(mode="after")
    def validate_distinct_property_names(self) -> "GraphSchemaType":
        property_names = [item.name for item in self.properties]
        if len(property_names) != len(set(property_names)):
            raise ValueError("graph schema property names must not contain duplicates")
        return self


class GraphSchemaTriple(BaseModel):
    """图谱 Schema 的一条允许三元组。"""

    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target_type: str = Field(min_length=1)

    @field_validator("source_type", "relation_type", "target_type")
    @classmethod
    def normalize_reference(cls, value: str) -> str:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("graph schema triple references cannot be blank")
        return normalized_value

    def as_tuple(self) -> tuple[str, str, str]:
        """返回用于去重和稳定版本计算的不可变三元组。"""
        return self.source_type, self.relation_type, self.target_type


class GraphIndexSchema(BaseModel):
    """Knowledge Base 节点索引阶段使用的受控图谱 Schema。"""

    model_config = ConfigDict(extra="forbid")

    entity_types: list[GraphSchemaType] = Field(min_length=1)
    relation_types: list[GraphSchemaType] = Field(min_length=1)
    allowed_triples: list[GraphSchemaTriple] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references_and_duplicates(self) -> "GraphIndexSchema":
        entity_names = [item.name for item in self.entity_types]
        relation_names = [item.name for item in self.relation_types]
        if len(entity_names) != len(set(entity_names)):
            raise ValueError("graph entity type names must not contain duplicates")
        if len(relation_names) != len(set(relation_names)):
            raise ValueError("graph relation type names must not contain duplicates")

        declared_entities = set(entity_names)
        declared_relations = set(relation_names)
        triples = [item.as_tuple() for item in self.allowed_triples]
        if len(triples) != len(set(triples)):
            raise ValueError("graph allowed triples must not contain duplicates")
        for source_type, relation_type, target_type in triples:
            if (
                source_type not in declared_entities
                or relation_type not in declared_relations
                or target_type not in declared_entities
            ):
                raise ValueError("graph allowed triples must only reference declared entity and relation types")
        return self


class GraphIndexConfig(BaseModel):
    """Knowledge Base 节点的图谱索引配置。

    节点 DSL 只接受 ``graph_index_config``。此模型不承载图检索运行参数、模型
    凭据或可编辑的图版本；启用时由规范化后的 Schema 和抽取模型配置计算稳定的
    ``graph_version``，关闭时仅保存 ``{"enabled": false}``。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    schema: GraphIndexSchema | None = None
    extract_model_config: GraphExtractModelConfig | None = None
    graph_version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def require_minimal_disabled_configuration(cls, data: Any) -> Any:
        if isinstance(data, Mapping) and data.get("enabled") is False and set(data) != {"enabled"}:
            raise ValueError("disabled graph_index_config must only contain enabled")
        return data

    @model_validator(mode="after")
    def require_complete_enabled_configuration(self) -> "GraphIndexConfig":
        if not self.enabled:
            if self.schema is not None or self.extract_model_config is not None:
                raise ValueError("disabled graph_index_config must only contain enabled")
            self.graph_version = None
            return self

        if self.schema is None or self.extract_model_config is None:
            raise ValueError("schema and extract_model_config are required when graph indexing is enabled")
        self.graph_version = compute_graph_version(self)
        return self


class KnowledgeIndexNodeData(BaseNodeData):
    """
    Knowledge index Node Data.
    """

    type: NodeType = KNOWLEDGE_INDEX_NODE_TYPE
    chunk_structure: str
    index_chunk_variable_selector: list[str]
    indexing_technique: str | None = None
    summary_index_setting: SummaryIndexSettingDict | None = None
    graph_index_config: GraphIndexConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_graph_rag_config_alias(cls, data: Any) -> Any:
        """拒绝旧 Dataset GraphRAG 字段，避免节点 DSL 混入检索运行参数。"""
        if isinstance(data, Mapping) and "graph_rag_config" in data:
            raise ValueError("graph_rag_config is not supported; use graph_index_config")
        return data

    @field_validator("summary_index_setting", mode="before")
    @classmethod
    def normalize_summary_index_setting(cls, v: Any) -> Any:
        """Treat disabled summary settings and missing prompts as runtime defaults."""
        if v is None:
            return None
        if isinstance(v, dict):
            if v.get("enable") is None:
                return None
            if v.get("summary_prompt") is None:
                v = {key: value for key, value in v.items() if key != "summary_prompt"}
        return v
