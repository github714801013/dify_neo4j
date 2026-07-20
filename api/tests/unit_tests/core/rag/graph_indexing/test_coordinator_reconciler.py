"""GraphIndexJobCoordinator 与 Reconciler 的单元测试。"""

from uuid import uuid4

import pytest

from core.rag.graph_indexing.coordinator import GraphIndexJobCoordinator
from core.rag.graph_indexing.reconciler import GraphIndexReconciler
from core.rag.graph_indexing.repositories import SqlAlchemyGraphIndexJobRepository
from libs.datetime_utils import naive_utc_now
from models.dataset import Document, DocumentSegment
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob


@pytest.fixture
def db_session(_unit_test_engine):
    from core.db.session_factory import create_session

    session = create_session()
    from models.base import TypeBase

    TypeBase.metadata.create_all(
        session.bind,
        tables=[
            DatasetGraphConfig.__table__,
            DatasetGraphIndexJob.__table__,
            Document.__table__,
            DocumentSegment.__table__,
        ],
    )
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def repository(db_session):
    return SqlAlchemyGraphIndexJobRepository(db_session)


@pytest.fixture
def coordinator(repository):
    return GraphIndexJobCoordinator(repository=repository)


@pytest.fixture
def reconciler(repository, coordinator):
    return GraphIndexReconciler(repository=repository, coordinator=coordinator)


def _make_config(*, tenant_id, dataset_id, graph_version="v1", enabled=True) -> DatasetGraphConfig:
    return DatasetGraphConfig(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        enabled=enabled,
        graph_version=graph_version,
    )


def _make_document(*, tenant_id, dataset_id, batch="b1", word_count=10, completed=True, archived=False) -> Document:
    return Document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        position=0,
        data_source_type="upload_file",
        batch=batch,
        name="doc",
        created_from="api",
        created_by=str(uuid4()),
        completed_at=naive_utc_now() if completed else None,
        indexing_status="completed" if completed else "indexing",
        word_count=word_count,
        updated_at=naive_utc_now(),
        archived=archived,
    )


class TestCoordinatorEnsureJob:
    def test_creates_job_without_dispatching_before_commit(self, coordinator, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()

        job_id = coordinator.ensure_job_for_document(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document=doc,
            graph_version="v1",
        )
        assert job_id is not None

    def test_duplicate_call_returns_none(self, coordinator, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()

        first = coordinator.ensure_job_for_document(
            tenant_id=tenant_id, dataset_id=dataset_id, document=doc, graph_version="v1"
        )
        second = coordinator.ensure_job_for_document(
            tenant_id=tenant_id, dataset_id=dataset_id, document=doc, graph_version="v1"
        )
        assert first is not None
        assert second is None

    def test_source_version_change_creates_new_job(self, coordinator, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id, word_count=10)
        db_session.add(doc)
        db_session.flush()
        first = coordinator.ensure_job_for_document(
            tenant_id=tenant_id, dataset_id=dataset_id, document=doc, graph_version="v1"
        )
        doc.word_count = 99
        db_session.flush()
        second = coordinator.ensure_job_for_document(
            tenant_id=tenant_id, dataset_id=dataset_id, document=doc, graph_version="v1"
        )
        assert first is not None
        assert second is not None
        assert first != second

    def test_graph_version_change_creates_new_job(self, coordinator, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        first = coordinator.ensure_job_for_document(
            tenant_id=tenant_id, dataset_id=dataset_id, document=doc, graph_version="v1"
        )
        second = coordinator.ensure_job_for_document(
            tenant_id=tenant_id, dataset_id=dataset_id, document=doc, graph_version="v2"
        )
        assert first is not None
        assert second is not None
        assert first != second


class TestReconcilerEnabledFlag:
    def test_disabled_global_flag_skips(self, reconciler, db_session, monkeypatch):
        monkeypatch.setattr(reconciler, "_is_enabled", lambda: False)
        config = _make_config(tenant_id=str(uuid4()), dataset_id=str(uuid4()))
        db_session.add(config)
        doc = _make_document(tenant_id=config.tenant_id, dataset_id=config.dataset_id)
        db_session.add(doc)
        db_session.flush()

        dispatchable = reconciler.reconcile()
        jobs = db_session.query(DatasetGraphIndexJob).all()
        assert jobs == []
        assert dispatchable == []

    def test_only_enabled_configs_processed(self, reconciler, db_session, monkeypatch):
        monkeypatch.setattr(reconciler, "_is_enabled", lambda: True)
        tenant_id = str(uuid4())
        enabled = _make_config(tenant_id=tenant_id, dataset_id=str(uuid4()), enabled=True)
        disabled = _make_config(tenant_id=tenant_id, dataset_id=str(uuid4()), enabled=False)
        db_session.add_all([enabled, disabled])
        db_session.flush()
        doc_enabled = _make_document(tenant_id=tenant_id, dataset_id=enabled.dataset_id)
        doc_disabled = _make_document(tenant_id=tenant_id, dataset_id=disabled.dataset_id)
        db_session.add_all([doc_enabled, doc_disabled])
        db_session.flush()

        dispatchable = reconciler.reconcile()
        jobs = db_session.query(DatasetGraphIndexJob).all()
        assert {j.dataset_id for j in jobs} == {enabled.dataset_id}
        assert dispatchable == [jobs[0].id]

    def test_only_completed_non_archived_documents_processed(self, reconciler, db_session, monkeypatch):
        monkeypatch.setattr(reconciler, "_is_enabled", lambda: True)
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        config = _make_config(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(config)
        db_session.flush()

        completed = _make_document(tenant_id=tenant_id, dataset_id=dataset_id, completed=True)
        indexing = _make_document(tenant_id=tenant_id, dataset_id=dataset_id, completed=False)
        archived = _make_document(tenant_id=tenant_id, dataset_id=dataset_id, completed=True, archived=True)
        db_session.add_all([completed, indexing, archived])
        db_session.flush()

        dispatchable = reconciler.reconcile()
        jobs = db_session.query(DatasetGraphIndexJob).all()
        assert {j.document_id for j in jobs} == {completed.id}
        assert dispatchable == [jobs[0].id]

    def test_reconcile_is_idempotent_across_runs(self, reconciler, db_session, monkeypatch):
        monkeypatch.setattr(reconciler, "_is_enabled", lambda: True)
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        config = _make_config(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(config)
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()

        first_dispatch = reconciler.reconcile()
        second_dispatch = reconciler.reconcile()
        jobs = db_session.query(DatasetGraphIndexJob).all()
        assert len(jobs) == 1
        assert first_dispatch == [jobs[0].id]
        assert second_dispatch == [jobs[0].id]

    def test_tenant_isolation(self, reconciler, db_session, monkeypatch):
        monkeypatch.setattr(reconciler, "_is_enabled", lambda: True)
        tenant_a, tenant_b = str(uuid4()), str(uuid4())
        dataset_a, dataset_b = str(uuid4()), str(uuid4())
        config_a = _make_config(tenant_id=tenant_a, dataset_id=dataset_a)
        config_b = _make_config(tenant_id=tenant_b, dataset_id=dataset_b)
        db_session.add_all([config_a, config_b])
        doc_a = _make_document(tenant_id=tenant_a, dataset_id=dataset_a)
        doc_b = _make_document(tenant_id=tenant_b, dataset_id=dataset_b)
        db_session.add_all([doc_a, doc_b])
        db_session.flush()

        dispatchable = reconciler.reconcile()
        jobs = db_session.query(DatasetGraphIndexJob).all()
        by_tenant = {j.tenant_id for j in jobs}
        assert by_tenant == {tenant_a, tenant_b}
        assert set(dispatchable) == {job.id for job in jobs}
        for j in jobs:
            if j.tenant_id == tenant_a:
                assert j.dataset_id == dataset_a
                assert j.document_id == doc_a.id
            else:
                assert j.dataset_id == dataset_b
                assert j.document_id == doc_b.id

    def test_history_completed_documents_batched(self, reconciler, db_session, monkeypatch):
        monkeypatch.setattr(reconciler, "_is_enabled", lambda: True)
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        config = _make_config(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(config)
        db_session.flush()
        docs = [_make_document(tenant_id=tenant_id, dataset_id=dataset_id) for _ in range(3)]
        db_session.add_all(docs)
        db_session.flush()

        dispatchable = reconciler.reconcile()
        jobs = db_session.query(DatasetGraphIndexJob).all()
        assert {j.document_id for j in jobs} == {d.id for d in docs}
        assert set(dispatchable) == {job.id for job in jobs}
