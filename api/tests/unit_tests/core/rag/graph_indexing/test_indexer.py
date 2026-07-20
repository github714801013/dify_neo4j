"""GraphRAG Indexer 编排、版本切换与结果映射测试。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.rag.graph.entities import GraphExtractModelConfig, GraphSchema
from core.rag.graph_indexing.entities import GraphIndexJobStatus
from core.rag.graph_indexing.extractor import ExtractionResult, GraphExtractionError
from core.rag.graph_indexing.indexer import GraphIndexJobRequest, run_indexer
from core.rag.graph_indexing.neo4j_writer import GraphWriteError
from core.rag.graph_indexing.versioning import build_source_facts, compute_source_version
from libs.datetime_utils import naive_utc_now


@pytest.fixture(autouse=True)
def _current_source_by_default(request):
    if request.node.name == "test_source_version_current_uses_scoped_document_facts":
        yield
        return

    from core.rag.graph_indexing import indexer as module

    with patch.object(module, "_is_source_version_current", return_value=True):
        yield


def _job() -> GraphIndexJobRequest:
    return GraphIndexJobRequest(
        id="job-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="document-1",
        graph_version="v1",
        source_version="source-v2",
    )


def _index_input(module):
    return module._IndexInput(
        schema=GraphSchema.default(),
        extract_model=GraphExtractModelConfig(
            provider="provider",
            model="model",
            strict=False,
            max_triplets_per_chunk=7,
        ),
        segments=(module._SegmentInput(id="segment-1", content="product module"),),
    )


def test_source_version_current_uses_scoped_document_facts():
    from core.rag.graph_indexing import indexer as module

    now = naive_utc_now()
    document = SimpleNamespace(
        id="document-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        enabled=True,
        archived=False,
        indexing_status="completed",
        updated_at=now,
        completed_at=now,
        batch="batch-1",
        word_count=12,
    )
    source_version = compute_source_version(
        build_source_facts(
            document.id,
            updated_at=document.updated_at,
            completed_at=document.completed_at,
            batch=document.batch,
            word_count=document.word_count,
        )
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = document

    with (
        patch.object(module, "Session", return_value=session),
        patch.object(module, "db", SimpleNamespace(engine=object())),
    ):
        assert module._is_source_version_current(replace(_job(), source_version=source_version)) is True
        assert module._is_source_version_current(replace(_job(), source_version="stale")) is False


def test_load_index_input_scopes_segments_by_tenant_dataset_and_document():
    from core.rag.graph_indexing import indexer as module

    statements: list[str] = []
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.side_effect = lambda statement: (
        statements.append(str(statement))
        or SimpleNamespace(
            enabled=True,
            schema_json=GraphSchema.default().model_dump(mode="json"),
            extract_model_config=GraphExtractModelConfig(provider="provider", model="model").model_dump(mode="json"),
        )
    )
    scalar_result = MagicMock()
    scalar_result.all.return_value = []
    session.scalars.side_effect = lambda statement: statements.append(str(statement)) or scalar_result

    with (
        patch.object(module, "Session", return_value=session),
        patch.object(module, "db", SimpleNamespace(engine=object())),
    ):
        result = module._load_index_input(_job())

    assert result is not None
    segment_statement = statements[1]
    assert "document_segments.tenant_id" in segment_statement
    assert "document_segments.dataset_id" in segment_statement
    assert "document_segments.document_id" in segment_statement


def test_stale_source_before_writing_discards_partial_graph_and_cancels():
    from core.rag.graph_indexing import indexer as module

    with (
        patch.object(module, "_is_source_version_current", return_value=False),
        patch.object(module, "discard_document_version") as discard,
        patch.object(module, "_load_index_input") as load_input,
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.CANCELLED
    assert outcome.error_code == "graph_indexer_source_stale"
    load_input.assert_not_called()
    discard.assert_called_once_with(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="document-1",
        graph_version="v1",
        source_version="source-v2",
    )


def test_source_change_during_writing_discards_new_graph_without_finalizing():
    from core.rag.graph_indexing import indexer as module

    with (
        patch.object(module, "_is_source_version_current", side_effect=[True, False]),
        patch.object(module, "_load_index_input", return_value=_index_input(module)),
        patch.object(module, "ensure_graph_schema"),
        patch.object(module, "extract_with_llm", return_value=ExtractionResult()),
        patch.object(module, "write_segment", return_value=0),
        patch.object(module, "discard_document_version") as discard,
        patch.object(module, "finalize_document_version") as finalize,
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.CANCELLED
    discard.assert_called_once()
    finalize.assert_not_called()


def test_missing_config_is_terminal_failure():
    from core.rag.graph_indexing import indexer as module

    with patch.object(module, "_load_index_input", return_value=None):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.FAILED
    assert outcome.error_code == "graph_indexer_config_missing"


def test_success_initializes_schema_writes_segments_then_finalizes_version():
    from core.rag.graph_indexing import indexer as module

    events: list[str] = []
    with (
        patch.object(module, "_load_index_input", return_value=_index_input(module)),
        patch.object(module, "ensure_graph_schema", side_effect=lambda: events.append("schema")),
        patch.object(
            module,
            "extract_with_llm",
            side_effect=lambda **_kwargs: events.append("extract") or ExtractionResult(),
        ) as extract,
        patch.object(module, "write_segment", side_effect=lambda **_kwargs: events.append("write") or 0) as write,
        patch.object(
            module,
            "finalize_document_version",
            side_effect=lambda **_kwargs: events.append("finalize"),
        ) as finalize,
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.SUCCEEDED
    assert events == ["schema", "extract", "write", "finalize"]
    assert extract.call_args.kwargs["strict"] is False
    assert extract.call_args.kwargs["max_triplets_per_chunk"] == 7
    assert write.call_args.kwargs["source_version"] == "source-v2"
    finalize.assert_called_once_with(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="document-1",
        graph_version="v1",
        source_version="source-v2",
    )


def test_empty_document_still_finalizes_version_to_remove_old_graph():
    from core.rag.graph_indexing import indexer as module

    empty_input = module._IndexInput(
        schema=GraphSchema.default(),
        extract_model=GraphExtractModelConfig(provider="provider", model="model"),
        segments=(),
    )
    with (
        patch.object(module, "_load_index_input", return_value=empty_input),
        patch.object(module, "ensure_graph_schema"),
        patch.object(module, "write_segment") as write,
        patch.object(module, "finalize_document_version") as finalize,
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.SUCCEEDED
    write.assert_not_called()
    finalize.assert_called_once()


def test_extraction_error_is_retryable_and_does_not_finalize():
    from core.rag.graph_indexing import indexer as module

    with (
        patch.object(module, "_load_index_input", return_value=_index_input(module)),
        patch.object(module, "ensure_graph_schema"),
        patch.object(module, "extract_with_llm", side_effect=GraphExtractionError("bad output")),
        patch.object(module, "finalize_document_version") as finalize,
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.RETRY_WAITING
    assert outcome.error_code == "graph_indexer_extract_failed"
    finalize.assert_not_called()


def test_missing_neo4j_config_is_terminal_failure_and_does_not_finalize():
    from core.rag.graph_indexing import indexer as module

    with (
        patch.object(module, "_load_index_input", return_value=_index_input(module)),
        patch.object(module, "ensure_graph_schema"),
        patch.object(module, "extract_with_llm", return_value=ExtractionResult()),
        patch.object(module, "write_segment", side_effect=GraphWriteError("neo4j connection config is missing")),
        patch.object(module, "finalize_document_version") as finalize,
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.FAILED
    assert outcome.error_code == "graph_indexer_neo4j_unconfigured"
    finalize.assert_not_called()


def test_temporary_neo4j_error_is_retryable_and_preserves_old_version():
    from core.rag.graph_indexing import indexer as module

    with (
        patch.object(module, "_load_index_input", return_value=_index_input(module)),
        patch.object(module, "ensure_graph_schema"),
        patch.object(module, "extract_with_llm", return_value=ExtractionResult()),
        patch.object(module, "write_segment", side_effect=GraphWriteError("neo4j write failed: timeout")),
        patch.object(module, "finalize_document_version") as finalize,
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.RETRY_WAITING
    assert outcome.error_code == "graph_indexer_write_failed"
    finalize.assert_not_called()


def test_finalize_failure_is_retryable():
    from core.rag.graph_indexing import indexer as module

    with (
        patch.object(module, "_load_index_input", return_value=_index_input(module)),
        patch.object(module, "ensure_graph_schema"),
        patch.object(module, "extract_with_llm", return_value=ExtractionResult()),
        patch.object(module, "write_segment", return_value=0),
        patch.object(module, "finalize_document_version", side_effect=GraphWriteError("cleanup timeout")),
    ):
        outcome = run_indexer(_job())

    assert outcome.status == GraphIndexJobStatus.RETRY_WAITING
    assert outcome.error_code == "graph_indexer_write_failed"
