from uuid import uuid4

import pytest

from configs.feature import GraphRAGConfig
from core.rag.graph.entities import GraphQueryMode
from models.dataset_graph_config import DatasetGraphConfig


class TestDatasetGraphConfig:
    def test_defaults_keep_graphrag_disabled(self):
        # 准备并执行
        config = DatasetGraphConfig(
            tenant_id=str(uuid4()),
            dataset_id=str(uuid4()),
        )

        # 断言
        assert config.enabled is False
        assert config.query_mode == GraphQueryMode.HYBRID
        assert config.graph_top_k == 10
        assert config.graph_max_depth == 1
        assert config.graph_timeout_ms == 1500
        assert config.graph_weight == 0.3
        assert config.graph_version == "v1"

    def test_database_constraint_scopes_configuration_by_tenant_and_dataset(self):
        # 准备并执行
        constraint_names = {constraint.name for constraint in DatasetGraphConfig.__table__.constraints}

        # 断言
        assert "dataset_graph_config_tenant_dataset_unique" in constraint_names


class TestGraphRAGConfig:
    def test_global_feature_flag_is_disabled_without_connection_settings(self, monkeypatch):
        # 准备
        for variable_name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
            monkeypatch.delenv(variable_name, raising=False)

        # 执行
        config = GraphRAGConfig()

        # 断言
        assert config.GRAPH_RAG_ENABLED is False
        assert config.GRAPH_RAG_FAIL_OPEN is True
        assert config.GRAPH_RAG_DEFAULT_TIMEOUT_MS == 1500
        assert config.GRAPH_INDEX_WORKER_CONCURRENCY == 2
        assert config.GRAPH_INDEX_MAX_RETRIES == 5
        assert config.NEO4J_URI is None
        assert config.NEO4J_USERNAME is None
        assert config.NEO4J_PASSWORD is None
        assert config.NEO4J_DATABASE is None

    def test_reads_neo4j_connection_from_environment(self, monkeypatch):
        # 准备
        monkeypatch.setenv("NEO4J_URI", "bolt://neo4j.example:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "graph_user")
        monkeypatch.setenv("NEO4J_PASSWORD", "graph_password")
        monkeypatch.setenv("NEO4J_DATABASE", "graph_database")

        # 执行
        config = GraphRAGConfig()

        # 断言
        assert config.NEO4J_URI == "bolt://neo4j.example:7687"
        assert config.NEO4J_USERNAME == "graph_user"
        assert config.NEO4J_PASSWORD == "graph_password"
        assert config.NEO4J_DATABASE == "graph_database"


@pytest.mark.parametrize(
    ("index_enabled", "legacy_enabled", "extract_model_config", "expected"),
    [
        (None, True, {"provider": "openai", "model": "gpt-4.1-mini"}, True),
        (None, True, None, False),
        (False, True, {"provider": "openai", "model": "gpt-4.1-mini"}, False),
        (True, False, None, True),
    ],
)
def test_graph_indexing_enabled_supports_explicit_and_legacy_configs(
    index_enabled: bool | None,
    legacy_enabled: bool,
    extract_model_config: dict[str, str] | None,
    expected: bool,
) -> None:
    """新版抽取开关优先，历史记录继续沿用旧的启用语义。"""
    config = DatasetGraphConfig(
        tenant_id=str(uuid4()),
        dataset_id=str(uuid4()),
        enabled=legacy_enabled,
        index_enabled=index_enabled,
        extract_model_config=extract_model_config,
    )

    assert config.is_graph_indexing_enabled is expected
