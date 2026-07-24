from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

from core.rag.entities import KeywordSetting, VectorSetting
from core.rag.retrieval.retrieval_methods import RetrievalMethod
from core.workflow.nodes.knowledge_index.entities import GraphIndexConfig


class RerankingModelConfig(BaseModel):
    """
    Reranking Model Config.
    """

    reranking_provider_name: str | None = ""
    reranking_model_name: str | None = ""


class WeightedScoreConfig(BaseModel):
    """
    Weighted score Config.
    """

    vector_setting: VectorSetting | None
    keyword_setting: KeywordSetting | None


class IconInfo(BaseModel):
    icon: str
    icon_background: str | None = None
    icon_type: str | None = None
    icon_url: str | None = None


class PipelineTemplateInfoEntity(BaseModel):
    name: str
    description: str
    icon_info: IconInfo


class RagPipelineDatasetCreateEntity(BaseModel):
    name: str
    description: str
    icon_info: IconInfo
    permission: str
    partial_member_list: list[dict[str, str]] | None = None
    yaml_content: str | None = None


class RetrievalSetting(BaseModel):
    """
    Retrieval Setting.
    """

    search_method: RetrievalMethod
    top_k: int
    score_threshold: float | None = 0.5
    score_threshold_enabled: bool = False
    reranking_mode: str | None = "reranking_model"
    reranking_enable: bool | None = True
    reranking_model: RerankingModelConfig | None = None
    weights: WeightedScoreConfig | None = None


class KnowledgeConfiguration(BaseModel):
    """
    Knowledge Base Configuration.
    """

    chunk_structure: str
    indexing_technique: Literal["high_quality", "economy"]
    embedding_model_provider: str = ""
    embedding_model: str = ""
    keyword_number: int | None = 10
    retrieval_model: RetrievalSetting
    # add summary index setting
    summary_index_setting: dict[str, object] | None = None
    graph_index_config: GraphIndexConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_graph_rag_config_alias(cls, data: Any) -> Any:
        """拒绝节点 DSL 的旧图检索别名，防止导入和发布绕过节点契约。"""
        if isinstance(data, Mapping) and "graph_rag_config" in data:
            raise ValueError("graph_rag_config is not supported; use graph_index_config")
        return data

    @field_validator("embedding_model_provider", "embedding_model", mode="before")
    @classmethod
    def validate_embedding_model_fields(cls, v: str | None) -> str:
        if v is None:
            return ""
        return v
