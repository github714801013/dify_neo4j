"""graph_indexing_task 与 graph_reconcile_task 的单元测试。"""

import pathlib
from unittest.mock import MagicMock, patch


class TestGraphIndexingTask:
    def test_uses_graph_index_queue(self):
        from tasks.graph_indexing_task import graph_indexing_task

        assert graph_indexing_task.queue == "graph_index"

    def test_marks_retry_waiting_when_adapter_not_implemented(self):
        from core.rag.graph_indexing.entities import GraphIndexJobStatus
        from tasks.graph_indexing_task import graph_indexing_task

        mock_job = MagicMock()
        mock_job.id = "job-1"
        mock_job.status = GraphIndexJobStatus.PENDING
        mock_repository = MagicMock()
        mock_repository.claim.return_value = mock_job

        with (
            patch("tasks.graph_indexing_task.db") as mock_db,
            patch(
                "tasks.graph_indexing_task.SqlAlchemyGraphIndexJobRepository",
                return_value=mock_repository,
            ),
        ):
            cm = MagicMock()
            cm.__enter__.return_value = mock_db.session
            cm.__exit__.return_value = None
            mock_db.session.begin.return_value = cm

            graph_indexing_task.run("job-1")

        mock_repository.claim.assert_called_once_with("job-1")
        mock_repository.mark_retry_waiting.assert_called_once()
        args, kwargs = mock_repository.mark_retry_waiting.call_args
        assert kwargs.get("error_code") == "graph_indexer_not_implemented"
        assert mock_repository.mark_succeeded.called is False

    def test_skips_when_job_not_claimable(self):
        from core.rag.graph_indexing.errors import GraphIndexJobClaimError
        from tasks.graph_indexing_task import graph_indexing_task

        mock_repository = MagicMock()
        mock_repository.claim.side_effect = GraphIndexJobClaimError("nope")

        with (
            patch("tasks.graph_indexing_task.db") as mock_db,
            patch(
                "tasks.graph_indexing_task.SqlAlchemyGraphIndexJobRepository",
                return_value=mock_repository,
            ),
        ):
            cm = MagicMock()
            cm.__enter__.return_value = mock_db.session
            cm.__exit__.return_value = None
            mock_db.session.begin.return_value = cm

            graph_indexing_task.run("job-1")

        mock_repository.mark_retry_waiting.assert_not_called()
        mock_repository.mark_succeeded.assert_not_called()


class TestGraphReconcileTask:
    def test_uses_graph_index_queue(self):
        from schedule.graph_reconcile_task import graph_reconcile_task

        assert graph_reconcile_task.queue == "graph_index"

    def test_skips_when_flags_disabled(self):
        from schedule.graph_reconcile_task import graph_reconcile_task

        with (
            patch("schedule.graph_reconcile_task.dify_config") as mock_config,
            patch("schedule.graph_reconcile_task.db") as mock_db,
        ):
            mock_config.GRAPH_RAG_ENABLED = False
            mock_config.ENABLE_GRAPH_RECONCILE_TASK = True
            graph_reconcile_task.run()
            mock_db.session.begin.assert_not_called()

    def test_invokes_reconciler_when_enabled(self):
        from schedule import graph_reconcile_task as module

        mock_reconciler = MagicMock()
        mock_session = MagicMock()
        cm = MagicMock()
        cm.__enter__.return_value = mock_session
        cm.__exit__.return_value = None
        mock_db = MagicMock()
        mock_db.session.begin.return_value = cm

        with (
            patch.object(module, "dify_config") as mock_config,
            patch.object(module, "db", mock_db),
            patch.object(module, "_build_reconciler", return_value=mock_reconciler),
        ):
            mock_config.GRAPH_RAG_ENABLED = True
            mock_config.ENABLE_GRAPH_RECONCILE_TASK = True
            module.graph_reconcile_task.run()

        mock_reconciler.reconcile.assert_called_once()


class TestBeatRegistration:
    def test_config_flag_defaults_disabled(self):
        from configs import dify_config

        assert dify_config.ENABLE_GRAPH_RECONCILE_TASK is False

    def test_ext_celery_registers_beat_entry(self):
        import api.extensions.ext_celery as ext_module

        source = pathlib.Path(ext_module.__file__).read_text(encoding="utf-8")
        assert "ENABLE_GRAPH_RECONCILE_TASK" in source
        assert "schedule.graph_reconcile_task.graph_reconcile_task" in source
        assert "GRAPH_RECONCILE_INTERVAL_MINUTES" in source
