import pytest
from pydantic import ValidationError

from core.rag.graph.entities import (
    DEFAULT_DOCUMENT_GRAPH_SCHEMA,
    DEFAULT_GRAPH_SCHEMA,
    GraphExtractionConfig,
    GraphExtractModelConfig,
    GraphQuery,
    GraphQueryDirection,
    GraphRetrievalConfig,
    GraphSchema,
)


class TestGraphSchema:
    def test_default_schema_only_references_declared_types(self):
        # 准备并执行
        schema = GraphSchema.default()

        # 断言
        assert schema == DEFAULT_GRAPH_SCHEMA
        assert schema.allowed_triples
        assert all(source in schema.entity_types for source, _, _ in schema.allowed_triples)
        assert all(relation in schema.relation_types for _, relation, _ in schema.allowed_triples)
        assert all(target in schema.entity_types for _, _, target in schema.allowed_triples)

    def test_schema_rejects_duplicate_entity_type(self):
        # 准备、执行并断言
        with pytest.raises(ValidationError, match="entity_types"):
            GraphSchema(
                entity_types=["PRODUCT", "PRODUCT"],
                relation_types=["CONTAINS"],
                allowed_triples=[("PRODUCT", "CONTAINS", "PRODUCT")],
            )

    def test_schema_rejects_triple_with_undeclared_relation(self):
        # 准备、执行并断言
        with pytest.raises(ValidationError, match="allowed_triples"):
            GraphSchema(
                entity_types=["PRODUCT"],
                relation_types=["CONTAINS"],
                allowed_triples=[("PRODUCT", "DEPENDS_ON", "PRODUCT")],
            )


class TestGraphQuery:
    def test_query_restricts_first_release_to_one_hop(self):
        # 准备并执行
        query = GraphQuery(
            entity_names=["Dify"],
            relation_types=["CONTAINS"],
            direction=GraphQueryDirection.OUTBOUND,
            max_depth=1,
            limit=10,
        )

        # 断言
        assert query.max_depth == 1

    def test_query_rejects_multi_hop_expansion(self):
        # 准备、执行并断言
        with pytest.raises(ValidationError, match="max_depth"):
            GraphQuery(
                entity_names=["Dify"],
                relation_types=["CONTAINS"],
                direction=GraphQueryDirection.OUTBOUND,
                max_depth=2,
                limit=10,
            )


class TestGraphRetrievalConfig:
    def test_retrieval_config_rejects_extraction_settings(self):
        # 准备
        config = {
            "enabled": True,
            "extract_model_config": {
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        }

        # 执行与断言
        with pytest.raises(ValidationError):
            GraphRetrievalConfig.model_validate(config)


class TestGraphExtractionConfig:
    def test_disabled_config_allows_incomplete_draft(self):
        # 准备并执行
        config = GraphExtractionConfig(enabled=False)

        # 断言
        assert config.schema is None
        assert config.extract_model_config is None
        assert config.graph_version is None

    def test_enabled_config_requires_complete_extraction_settings(self):
        # 准备、执行并断言
        with pytest.raises(ValidationError, match="schema and extract_model_config"):
            GraphExtractionConfig(enabled=True)

    def test_enabled_config_generates_stable_version_from_document_schema(self):
        # 准备
        extract_model_config = GraphExtractModelConfig(provider="openai", model="gpt-4o-mini")

        # 执行
        config = GraphExtractionConfig(
            enabled=True,
            schema=DEFAULT_DOCUMENT_GRAPH_SCHEMA,
            extract_model_config=extract_model_config,
        )
        same_config = GraphExtractionConfig(
            enabled=True,
            schema=DEFAULT_DOCUMENT_GRAPH_SCHEMA,
            extract_model_config=extract_model_config,
        )

        # 断言
        assert config.graph_version is not None
        assert config.graph_version == same_config.graph_version

    def test_extraction_config_rejects_retrieval_settings(self):
        # 准备、执行并断言
        with pytest.raises(ValidationError):
            GraphExtractionConfig.model_validate({"enabled": False, "graph_top_k": 20})