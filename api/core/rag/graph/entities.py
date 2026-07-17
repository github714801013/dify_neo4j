"""供后续索引和检索阶段复用的已校验 GraphRAG 契约。

首版只支持由固定模板生成的一跳图查询。本模块只描述数据；外部图数据库和
LLM 调用属于后续基础设施适配层。
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, field_validator, model_validator

DEFAULT_ENTITY_TYPES = (
    "PRODUCT",
    "MODULE",
    "FEATURE",
    "VERSION",
    "API",
    "PARAMETER",
    "ERROR",
    "DOCUMENT",
)
DEFAULT_RELATION_TYPES = (
    "CONTAINS",
    "SUPPORTS",
    "DEPENDS_ON",
    "AVAILABLE_IN",
    "CONFIGURES",
    "CALLS",
    "RETURNS",
    "CAUSES",
    "SOLVES",
    "DESCRIBES",
)
DEFAULT_ALLOWED_TRIPLES = (
    ("PRODUCT", "CONTAINS", "MODULE"),
    ("MODULE", "CONTAINS", "FEATURE"),
    ("FEATURE", "AVAILABLE_IN", "VERSION"),
    ("FEATURE", "CONFIGURES", "PARAMETER"),
    ("API", "CALLS", "API"),
    ("ERROR", "CAUSES", "FEATURE"),
    ("ERROR", "SOLVES", "FEATURE"),
    ("DOCUMENT", "DESCRIBES", "PRODUCT"),
    ("DOCUMENT", "DESCRIBES", "MODULE"),
    ("DOCUMENT", "DESCRIBES", "FEATURE"),
)


class GraphQueryMode(StrEnum):
    """GraphRAG 计划支持的 Dataset 级检索模式。"""

    VECTOR = "vector"
    HYBRID = "hybrid"


class GraphExtractModelConfig(BaseModel):
    """LlamaIndex SchemaLLMPathExtractor 的结构化模型配置。"""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(default=0, ge=0, le=2)
    max_tokens: int | None = Field(
        default=None, ge=1, le=131072, description="LLM 输出 token 上限；为空时复用提供商模型默认值"
    )
    max_triplets_per_chunk: int = Field(default=10, ge=1, le=50)
    strict: StrictBool = True

    @field_validator("provider", "model")
    @classmethod
    def strip_model_names(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("provider and model cannot be blank")
        return value


class GraphRagConfigInput(BaseModel):
    """创建 Dataset 时使用的 GraphRAG 配置输入。"""

    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool = False
    query_mode: GraphQueryMode = GraphQueryMode.HYBRID
    graph_top_k: int = Field(default=10, ge=1, le=100)
    graph_max_depth: int = Field(default=1, ge=1, le=5)
    graph_timeout_ms: int = Field(default=1500, ge=100, le=30_000)
    graph_weight: float = Field(default=0.3, ge=0, le=1)
    extract_model_config: GraphExtractModelConfig | None = None
    graph_version: str = Field(default="v1", min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_extractor_when_enabled(self) -> "GraphRagConfigInput":
        if self.enabled and self.extract_model_config is None:
            raise ValueError("extract_model_config is required when GraphRAG is enabled")
        return self


class GraphQueryDirection(StrEnum):
    """固定图查询模板允许的方向。"""

    OUTBOUND = "outbound"
    INBOUND = "inbound"
    BOTH = "both"


class GraphSchema(BaseModel):
    """单个 Dataset GraphRAG 配置允许使用的实体和关系词表。"""

    model_config = ConfigDict(extra="forbid")

    entity_types: list[str] = Field(min_length=1)
    relation_types: list[str] = Field(min_length=1)
    allowed_triples: list[tuple[str, str, str]] = Field(min_length=1)

    @field_validator("entity_types", "relation_types")
    @classmethod
    def validate_distinct_names(cls, values: list[str]) -> list[str]:
        normalized_values = [value.strip() for value in values]
        if any(not value for value in normalized_values):
            raise ValueError("entity_types and relation_types cannot contain blank values")
        if len(normalized_values) != len(set(normalized_values)):
            raise ValueError("entity_types and relation_types must not contain duplicates")
        return normalized_values

    @model_validator(mode="after")
    def validate_allowed_triples(self) -> "GraphSchema":
        declared_entities = set(self.entity_types)
        declared_relations = set(self.relation_types)
        for source, relation, target in self.allowed_triples:
            if source not in declared_entities or target not in declared_entities or relation not in declared_relations:
                raise ValueError("allowed_triples must only reference declared entity_types and relation_types")
        if len(self.allowed_triples) != len(set(self.allowed_triples)):
            raise ValueError("allowed_triples must not contain duplicates")
        return self

    @classmethod
    def default(cls) -> "GraphSchema":
        """返回首版固定 Schema 的独立副本。"""

        return cls.model_validate(DEFAULT_GRAPH_SCHEMA.model_dump(mode="json"))


DEFAULT_GRAPH_SCHEMA = GraphSchema(
    entity_types=list(DEFAULT_ENTITY_TYPES),
    relation_types=list(DEFAULT_RELATION_TYPES),
    allowed_triples=list(DEFAULT_ALLOWED_TRIPLES),
)


class GraphQuery(BaseModel):
    """固定且安全的图查询模板结构化输入。"""

    model_config = ConfigDict(extra="forbid")

    entity_names: list[str] = Field(min_length=1, max_length=5)
    relation_types: list[str] = Field(default_factory=list)
    direction: GraphQueryDirection = GraphQueryDirection.BOTH
    max_depth: int = Field(default=1, ge=1, le=1)
    limit: int = Field(default=10, ge=1, le=20)

    @field_validator("entity_names", "relation_types")
    @classmethod
    def strip_query_values(cls, values: list[str]) -> list[str]:
        normalized_values = [value.strip() for value in values]
        if any(not value for value in normalized_values):
            raise ValueError("graph query values cannot be blank")
        return normalized_values


class GraphResult(BaseModel):
    """与 Dify 检索结果融合前的图检索 Segment 候选项。"""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    graph_rank: int = Field(ge=1)
    graph_distance: int = Field(ge=0, le=1)
    graph_path: list[str] = Field(default_factory=list)


class KnowledgeCandidate(BaseModel):
    """供后续融合服务使用的来源无关检索候选项。"""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    content: str | None = None
    vector_rank: int | None = Field(default=None, ge=1)
    keyword_rank: int | None = Field(default=None, ge=1)
    graph_rank: int | None = Field(default=None, ge=1)
    graph_distance: int | None = Field(default=None, ge=0, le=1)
    graph_path: list[str] | None = None
    source_types: set[str] = Field(default_factory=set)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
