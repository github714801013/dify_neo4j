"""Graph Index Job 仓储的单元测试，使用 in-memory SQLite。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.rag.graph_indexing.entities import (
    GRAPH_INDEX_STALE_MINUTES,
    GraphIndexJobStatus,
)
from core.rag.graph_indexing.errors import (
    GraphIndexerNotImplementedError,
    GraphIndexJobClaimError,
    GraphIndexJobNotFoundError,
)
from core.rag.graph_indexing.repositories import SqlAlchemyGraphIndexJobRepository
from core.rag.graph_indexing.versioning import build_source_facts, compute_source_version
from libs.datetime_utils import naive_utc_now
from models.dataset import Document
from models.dataset_graph_config import DatasetGraphConfig
from models.dataset_graph_index_job import DatasetGraphIndexJob


@pytest.fixture
def db_session(_unit_test_engine):
    """每个测试独立的 in-memory SQLite session，并建好 GraphRAG 表。"""
    from core.db.session_factory import create_session

    session = create_session()
    from models.base import TypeBase

    TypeBase.metadata.create_all(
        session.bind,
        tables=[
            DatasetGraphConfig.__table__,
            DatasetGraphIndexJob.__table__,
            Document.__table__,
        ],
    )
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def repository(db_session):
    return SqlAlchemyGraphIndexJobRepository(db_session)


def _make_config(
    *, tenant_id: str, dataset_id: str, graph_version: str = "v1", enabled: bool = True
) -> DatasetGraphConfig:
    return DatasetGraphConfig(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        enabled=enabled,
        graph_version=graph_version,
    )


def _make_document(*, tenant_id: str, dataset_id: str, batch: str = "b1", word_count: int = 10) -> Document:
    return Document(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        position=0,
        data_source_type="upload_file",
        batch=batch,
        name="doc",
        created_from="api",
        created_by=str(uuid4()),
        completed_at=naive_utc_now(),
        indexing_status="completed",
        word_count=word_count,
        updated_at=naive_utc_now(),
    )


def _facts(document: Document):
    return build_source_facts(
        document.id,
        updated_at=document.updated_at,
        completed_at=document.completed_at,
        batch=document.batch,
        word_count=document.word_count,
    )


class TestCreateIfMissing:
    def test_creates_new_job(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()

        facts = _facts(doc)
        source_version = compute_source_version(facts)
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=source_version,
            graph_version="v1",
        )

        assert job is not None
        assert job.status == GraphIndexJobStatus.PENDING
        assert job.attempts == 0

    def test_duplicate_returns_none(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()

        source_version = compute_source_version(_facts(doc))
        repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=source_version,
            graph_version="v1",
        )
        second = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=source_version,
            graph_version="v1",
        )
        assert second is None

    def test_different_source_version_creates_separate_job(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id, word_count=10)
        db_session.add(doc)
        db_session.flush()

        v1 = compute_source_version(_facts(doc))
        doc.word_count = 20
        db_session.flush()
        v2 = compute_source_version(_facts(doc))
        assert v1 != v2

        repository.create_if_missing(
            tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc.id, source_version=v1, graph_version="v1"
        )
        repository.create_if_missing(
            tenant_id=tenant_id, dataset_id=dataset_id, document_id=doc.id, source_version=v2, graph_version="v1"
        )
        count = db_session.scalar(select(DatasetGraphIndexJob).where(DatasetGraphIndexJob.document_id == doc.id))
        jobs = db_session.scalars(select(DatasetGraphIndexJob).where(DatasetGraphIndexJob.document_id == doc.id)).all()
        assert {j.source_version for j in jobs} == {v1, v2}

    def test_different_graph_version_creates_separate_job(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        source_version = compute_source_version(_facts(doc))

        repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=source_version,
            graph_version="v1",
        )
        repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=source_version,
            graph_version="v2",
        )
        jobs = db_session.scalars(select(DatasetGraphIndexJob).where(DatasetGraphIndexJob.document_id == doc.id)).all()
        assert {j.graph_version for j in jobs} == {"v1", "v2"}


class TestClaimAndTransition:
    def test_claim_pending_to_running(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=compute_source_version(_facts(doc)),
            graph_version="v1",
        )
        db_session.flush()

        claimed = repository.claim(job.id)
        assert claimed.status == GraphIndexJobStatus.RUNNING
        assert claimed.locked_at is not None
        assert claimed.started_at is not None

    def test_claim_non_pending_raises(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=compute_source_version(_facts(doc)),
            graph_version="v1",
        )
        db_session.flush()
        repository.claim(job.id)

        with pytest.raises(GraphIndexJobClaimError):
            repository.claim(job.id)

    def test_claim_missing_job_raises(self, repository):
        with pytest.raises(GraphIndexJobNotFoundError):
            repository.claim("nonexistent")

    def test_mark_retry_waiting_from_running(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=compute_source_version(_facts(doc)),
            graph_version="v1",
        )
        db_session.flush()
        repository.claim(job.id)

        result = repository.mark_retry_waiting(
            job.id,
            error_code=GraphIndexerNotImplementedError.code,
            error_message="not impl",
            retry_base_seconds=30,
        )
        assert result.status == GraphIndexJobStatus.RETRY_WAITING
        assert result.attempts == 1
        assert result.last_error_code == GraphIndexerNotImplementedError.code

    def test_mark_failed_from_running(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=compute_source_version(_facts(doc)),
            graph_version="v1",
        )
        db_session.flush()
        repository.claim(job.id)
        result = repository.mark_failed(job.id, error_code="boom", error_message="bad")
        assert result.status == GraphIndexJobStatus.FAILED
        assert result.completed_at is not None

    def test_mark_succeeded_from_running(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=compute_source_version(_facts(doc)),
            graph_version="v1",
        )
        db_session.flush()
        repository.claim(job.id)
        result = repository.mark_succeeded(job.id)
        assert result.status == GraphIndexJobStatus.SUCCEEDED


class TestRequeueStale:
    def test_retry_waiting_always_recovered(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=compute_source_version(_facts(doc)),
            graph_version="v1",
        )
        db_session.flush()
        repository.claim(job.id)
        repository.mark_retry_waiting(
            job.id,
            error_code=GraphIndexerNotImplementedError.code,
            error_message="not impl",
            retry_base_seconds=30,
        )
        db_session.flush()

        recovered = repository.requeue_stale_running_jobs(stale_minutes=GRAPH_INDEX_STALE_MINUTES)
        assert recovered == 1
        refreshed = repository.get(job.id)
        assert refreshed.status == GraphIndexJobStatus.PENDING

    def test_running_only_recovered_when_stale(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version=compute_source_version(_facts(doc)),
            graph_version="v1",
        )
        db_session.flush()
        repository.claim(job.id)
        db_session.flush()

        # fresh running, not stale -> not recovered
        recovered = repository.requeue_stale_running_jobs(stale_minutes=GRAPH_INDEX_STALE_MINUTES)
        assert recovered == 0

        # make locked_at old
        job.locked_at = naive_utc_now() - timedelta(minutes=GRAPH_INDEX_STALE_MINUTES + 1)
        db_session.flush()
        recovered = repository.requeue_stale_running_jobs(stale_minutes=GRAPH_INDEX_STALE_MINUTES)
        assert recovered == 1
        assert repository.get(job.id).status == GraphIndexJobStatus.PENDING


class TestTenantIsolation:
    def test_list_completed_documents_scoped_by_tenant(self, repository, db_session):
        tenant_a, tenant_b = str(uuid4()), str(uuid4())
        dataset_a, dataset_b = str(uuid4()), str(uuid4())
        doc_a = _make_document(tenant_id=tenant_a, dataset_id=dataset_a)
        doc_b = _make_document(tenant_id=tenant_b, dataset_id=dataset_b)
        db_session.add_all([doc_a, doc_b])
        db_session.flush()

        docs_a = repository.list_completed_documents(tenant_id=tenant_a, dataset_id=dataset_a, limit=10, offset=0)
        assert [d.id for d in docs_a] == [doc_a.id]


class TestListEnabledGraphConfigs:
    def test_only_enabled_returned(self, repository, db_session):
        tenant_id = str(uuid4())
        enabled = _make_config(tenant_id=tenant_id, dataset_id=str(uuid4()), enabled=True)
        disabled = _make_config(tenant_id=tenant_id, dataset_id=str(uuid4()), enabled=False)
        db_session.add_all([enabled, disabled])
        db_session.flush()

        configs = repository.list_enabled_graph_configs(limit=10, offset=0)
        ids = {c.dataset_id for c in configs}
        assert enabled.dataset_id in ids
        assert disabled.dataset_id not in ids
