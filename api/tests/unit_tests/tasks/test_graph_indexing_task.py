"""GraphRAG Worker 与 Reconciler 任务的单元测试。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.rag.graph_indexing.entities import GraphIndexJobStatus
from core.rag.graph_indexing.errors import GraphIndexJobClaimError
from core.rag.graph_indexing.indexer import GraphIndexJobRequest, GraphIndexOutcome
from core.rag.graph_indexing.reconciler import GraphDatasetCleanupCommand, GraphReconcilePlan


class TestGraphIndexingTask:
    def test_uses_graph_index_queue(self):
        from tasks.graph_indexing_task import graph_indexing_task

        assert graph_indexing_task.queue == "graph_index"

    def test_claims_before_processing_and_persists_after_processing(self):
        from tasks import graph_indexing_task as module

        request = GraphIndexJobRequest(
            id="job-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="document-1",
            graph_version="v1",
            source_version="source-v1",
        )
        outcome = GraphIndexOutcome.succeeded()
        events: list[str] = []

        with (
            patch.object(module, "_claim_job", side_effect=lambda _job_id: events.append("claim") or request),
            patch.object(module, "run_indexer", side_effect=lambda _request: events.append("process") or outcome),
            patch.object(module, "_persist_outcome", side_effect=lambda _job_id, _outcome: events.append("persist")),
        ):
            module.graph_indexing_task.run("job-1")

        assert events == ["claim", "process", "persist"]

    def test_skips_when_job_not_claimable(self):
        from tasks import graph_indexing_task as module

        with (
            patch.object(module, "_claim_job", side_effect=GraphIndexJobClaimError("nope")),
            patch.object(module, "run_indexer") as run_indexer,
            patch.object(module, "_persist_outcome") as persist_outcome,
        ):
            module.graph_indexing_task.run("job-1")

        run_indexer.assert_not_called()
        persist_outcome.assert_not_called()

    def test_unexpected_indexer_error_is_persisted_as_retry(self):
        from tasks import graph_indexing_task as module

        request = GraphIndexJobRequest(
            id="job-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="document-1",
            graph_version="v1",
            source_version="source-v1",
        )
        with (
            patch.object(module, "_claim_job", return_value=request),
            patch.object(module, "run_indexer", side_effect=RuntimeError("boom")),
            patch.object(module, "_persist_outcome") as persist_outcome,
        ):
            module.graph_indexing_task.run("job-1")

        persisted = persist_outcome.call_args.args[1]
        assert persisted.status == GraphIndexJobStatus.RETRY_WAITING
        assert persisted.error_code == "graph_indexer_unexpected_error"

    def test_claim_helper_returns_detached_snapshot(self):
        from tasks import graph_indexing_task as module

        session = MagicMock()
        session_context = MagicMock()
        session_context.__enter__.return_value = session
        transaction_context = MagicMock()
        session.begin.return_value = transaction_context
        claimed_job = SimpleNamespace(
            id="job-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="document-1",
            graph_version="v1",
            source_version="source-v1",
        )
        repository = MagicMock()
        repository.claim.return_value = claimed_job

        with (
            patch.object(module, "Session", return_value=session_context),
            patch.object(module, "SqlAlchemyGraphIndexJobRepository", return_value=repository),
            patch.object(module, "db", SimpleNamespace(engine=object())),
        ):
            result = module._claim_job("job-1")

        assert result == GraphIndexJobRequest.from_job(claimed_job)
        repository.claim.assert_called_once_with("job-1")
        transaction_context.__exit__.assert_called_once()
        session_context.__exit__.assert_called_once()

    def test_cancelled_outcome_marks_job_cancelled(self):
        from tasks import graph_indexing_task as module

        session = MagicMock()
        session_context = MagicMock()
        session_context.__enter__.return_value = session
        session.begin.return_value = MagicMock()
        repository = MagicMock()
        outcome = GraphIndexOutcome.cancelled(
            error_code="graph_indexer_source_stale",
            error_message="source version changed",
        )

        with (
            patch.object(module, "Session", return_value=session_context),
            patch.object(module, "SqlAlchemyGraphIndexJobRepository", return_value=repository),
            patch.object(module, "db", SimpleNamespace(engine=object())),
        ):
            module._persist_outcome("job-1", outcome)

        repository.mark_cancelled.assert_called_once_with(
            "job-1",
            error_code="graph_indexer_source_stale",
            error_message="source version changed",
        )

    def test_retry_outcome_uses_configured_backoff_and_limit(self):
        from tasks import graph_indexing_task as module

        session = MagicMock()
        session_context = MagicMock()
        session_context.__enter__.return_value = session
        session.begin.return_value = MagicMock()
        repository = MagicMock()
        outcome = GraphIndexOutcome.retry(error_code="temporary", error_message="temporary")

        with (
            patch.object(module, "Session", return_value=session_context),
            patch.object(module, "SqlAlchemyGraphIndexJobRepository", return_value=repository),
            patch.object(module, "db", SimpleNamespace(engine=object())),
            patch.object(module.dify_config, "GRAPH_INDEX_RETRY_BASE_SECONDS", 15),
            patch.object(module.dify_config, "GRAPH_INDEX_MAX_RETRIES", 4),
        ):
            module._persist_outcome("job-1", outcome)

        repository.mark_retry_waiting.assert_called_once_with(
            "job-1",
            error_code="temporary",
            error_message="temporary",
            retry_base_seconds=15,
            max_retries=4,
        )


class TestGraphReconcileTask:
    def test_uses_graph_index_queue(self):
        from schedule.graph_reconcile_task import graph_reconcile_task

        assert graph_reconcile_task.queue == "graph_index"

    def test_skips_when_flags_disabled(self):
        from schedule import graph_reconcile_task as module

        with (
            patch.object(module, "dify_config") as config,
            patch.object(module, "_reconcile_jobs") as reconcile_jobs,
            patch.object(module, "_dispatch_jobs") as dispatch_jobs,
        ):
            config.GRAPH_RAG_ENABLED = False
            config.ENABLE_GRAPH_RECONCILE_TASK = True
            module.graph_reconcile_task.run()

        reconcile_jobs.assert_not_called()
        dispatch_jobs.assert_not_called()

    def test_reconcile_helper_closes_transaction_before_returning(self):
        from schedule import graph_reconcile_task as module

        session = MagicMock()
        session_context = MagicMock()
        session_context.__enter__.return_value = session
        transaction_context = MagicMock()
        session.begin.return_value = transaction_context
        repository = MagicMock()
        coordinator = MagicMock()
        reconciler = MagicMock()
        plan = GraphReconcilePlan(job_ids=("job-1",), cleanup_commands=())
        reconciler.reconcile_plan.return_value = plan

        with (
            patch.object(module, "Session", return_value=session_context),
            patch.object(module, "SqlAlchemyGraphIndexJobRepository", return_value=repository),
            patch.object(module, "GraphIndexJobCoordinator", return_value=coordinator),
            patch.object(module, "GraphIndexReconciler", return_value=reconciler),
            patch.object(module, "db", SimpleNamespace(engine=object())),
        ):
            result = module._reconcile_jobs()

        assert result == plan
        transaction_context.__exit__.assert_called_once()
        session_context.__exit__.assert_called_once()

    def test_dispatches_only_after_reconcile_returns(self):
        from schedule import graph_reconcile_task as module

        events: list[str] = []
        plan = GraphReconcilePlan(job_ids=("job-1",), cleanup_commands=())
        with (
            patch.object(module, "dify_config") as config,
            patch.object(module, "_reconcile_jobs", side_effect=lambda: events.append("committed") or plan),
            patch.object(module, "_reconcile_graph_data", side_effect=lambda _plan: events.append("cleaned")),
            patch.object(module, "_dispatch_jobs", side_effect=lambda _ids: events.append("dispatched")),
        ):
            config.GRAPH_RAG_ENABLED = True
            config.ENABLE_GRAPH_RECONCILE_TASK = True
            module.graph_reconcile_task.run()

        assert events == ["committed", "cleaned", "dispatched"]

    def test_lifecycle_cleanup_continues_after_one_dataset_failure(self):
        from schedule import graph_reconcile_task as module

        commands = (
            GraphDatasetCleanupCommand("tenant-1", "dataset-1", ("doc-1",), ("segment-1",)),
            GraphDatasetCleanupCommand("tenant-1", "dataset-2", ("doc-2",), ("segment-2",)),
        )
        plan = GraphReconcilePlan(job_ids=(), cleanup_commands=commands)
        reconcile = MagicMock(side_effect=[RuntimeError("boom"), None])

        with patch.object(module, "reconcile_dataset_graph", reconcile):
            module._reconcile_graph_data(plan)

        assert reconcile.call_count == 2

    def test_dispatch_failure_does_not_stop_remaining_jobs(self):
        from schedule import graph_reconcile_task as module

        dispatcher = MagicMock(side_effect=[RuntimeError("boom"), None])
        with patch.object(module, "_CeleryDispatcher", return_value=dispatcher):
            module._dispatch_jobs(["job-1", "job-2"])

        assert dispatcher.call_args_list[0].args == ("job-1",)
        assert dispatcher.call_args_list[1].args == ("job-2",)


class TestBeatRegistration:
    def test_config_flag_defaults_disabled(self):
        from configs import dify_config

        assert dify_config.ENABLE_GRAPH_RECONCILE_TASK is False

    def test_ext_celery_registers_beat_entry(self):
        source = Path("api/extensions/ext_celery.py").read_text(encoding="utf-8")
        assert "ENABLE_GRAPH_RECONCILE_TASK" in source
        assert "schedule.graph_reconcile_task.graph_reconcile_task" in source
        assert "GRAPH_RECONCILE_INTERVAL_MINUTES" in source
