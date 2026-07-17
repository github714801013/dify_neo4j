import pytest
from pydantic import ValidationError

from core.rag.graph.entities import (
    DEFAULT_GRAPH_SCHEMA,
    GraphQuery,
    GraphQueryDirection,
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
