"""Graph Index Job 仓储的单元测试，使用 in-memory SQLite。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from core.rag.graph_indexing.entities import (
    DATASET_GRAPH_INDEX_SCOPE,
    GRAPH_INDEX_STALE_MINUTES,
    GraphIndexJobStatus,
)
from core.rag.graph_indexing.errors import (
    GraphIndexJobClaimError,
    GraphIndexJobNotFoundError,
)
from core.rag.graph_indexing.repositories import SqlAlchemyGraphIndexJobRepository
from core.rag.graph_indexing.scopes import PublishedNodeGraphScope
from core.rag.graph_indexing.versioning import build_source_facts, compute_source_version
from core.workflow.nodes.knowledge_index.entities import GraphIndexConfig
from libs.datetime_utils import naive_utc_now
from models.dataset import Document, DocumentSegment, SegmentStatus
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
            DocumentSegment.__table__,
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


def _make_segment(*, tenant_id: str, dataset_id: str, document_id: str) -> DocumentSegment:
    return DocumentSegment(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_id=document_id,
        position=0,
        content="segment",
        word_count=1,
        tokens=1,
        created_by=str(uuid4()),
        status=SegmentStatus.COMPLETED,
        completed_at=naive_utc_now(),
    )


def _facts(document: Document):
    return build_source_facts(
        document.id,
        updated_at=document.updated_at,
        completed_at=document.completed_at,
        batch=document.batch,
        word_count=document.word_count,
    )


def _node_scope(*, tenant_id: str, dataset_id: str, index_node_id: str, graph_version: str) -> PublishedNodeGraphScope:
    return PublishedNodeGraphScope(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        index_node_id=index_node_id,
        graph_version=graph_version,
        graph_config=GraphIndexConfig.model_construct(enabled=True, graph_version=graph_version),
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

    def test_different_node_scopes_create_separate_jobs(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        document = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(document)
        db_session.flush()
        source_version = compute_source_version(_facts(document))

        first = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version=source_version,
            graph_version="v1",
            index_node_id="node-a",
        )
        second = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version=source_version,
            graph_version="v1",
            index_node_id="node-b",
        )
        duplicate = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version=source_version,
            graph_version="v1",
            index_node_id="node-a",
        )

        assert first is not None
        assert second is not None
        assert duplicate is None
        assert {first.index_node_id, second.index_node_id} == {"node-a", "node-b"}


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

    def test_only_one_source_version_per_document_can_run(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        doc = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(doc)
        db_session.flush()
        first = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version="source-v1",
            graph_version="v1",
        )
        second = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=doc.id,
            source_version="source-v2",
            graph_version="v1",
        )
        db_session.flush()

        repository.claim(first.id)

        with pytest.raises(GraphIndexJobClaimError) as exc_info:
            repository.claim(second.id)

        assert exc_info.value.code == "graph_index_document_busy"
        assert repository.get(second.id).status == GraphIndexJobStatus.PENDING

    def test_claim_is_atomic_across_sessions(self, repository, db_session):
        from core.db.session_factory import create_session

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
        db_session.commit()

        first_session = create_session()
        second_session = create_session()
        try:
            first_repository = SqlAlchemyGraphIndexJobRepository(first_session)
            second_repository = SqlAlchemyGraphIndexJobRepository(second_session)

            first_repository.claim(job.id)
            first_session.commit()

            with pytest.raises(GraphIndexJobClaimError):
                second_repository.claim(job.id)
        finally:
            first_session.close()
            second_session.close()

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

        before = naive_utc_now()
        result = repository.mark_retry_waiting(
            job.id,
            error_code="temporary",
            error_message="temporary",
            retry_base_seconds=30,
            max_retries=5,
        )
        assert result.status == GraphIndexJobStatus.RETRY_WAITING
        assert result.attempts == 1
        assert result.last_error_code == "temporary"
        assert result.available_at >= before + timedelta(seconds=30)

    def test_retry_uses_exponential_backoff(self, repository, db_session):
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
        job.attempts = 1
        repository.claim(job.id)

        before = naive_utc_now()
        result = repository.mark_retry_waiting(
            job.id,
            error_code="temporary",
            error_message="temporary",
            retry_base_seconds=30,
            max_retries=5,
        )

        assert result.status == GraphIndexJobStatus.RETRY_WAITING
        assert result.attempts == 2
        assert result.available_at >= before + timedelta(seconds=60)

    def test_retry_limit_allows_configured_number_of_retries(self, repository, db_session):
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
        job.attempts = 4
        repository.claim(job.id)

        result = repository.mark_retry_waiting(
            job.id,
            error_code="temporary",
            error_message="temporary",
            retry_base_seconds=30,
            max_retries=5,
        )

        assert result.status == GraphIndexJobStatus.RETRY_WAITING
        assert result.attempts == 5

    def test_retry_limit_marks_job_failed(self, repository, db_session):
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
        job.attempts = 5
        repository.claim(job.id)

        result = repository.mark_retry_waiting(
            job.id,
            error_code="temporary",
            error_message="temporary",
            retry_base_seconds=30,
            max_retries=5,
        )

        assert result.status == GraphIndexJobStatus.FAILED
        assert result.attempts == 6
        assert result.completed_at is not None

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

    def test_mark_cancelled_from_running(self, repository, db_session):
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

        result = repository.mark_cancelled(
            job.id,
            error_code="graph_indexer_source_stale",
            error_message="source version changed",
        )

        assert result.status == GraphIndexJobStatus.CANCELLED
        assert result.completed_at is not None
        assert result.last_error_code == "graph_indexer_source_stale"

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
    def test_retry_waiting_recovered_only_after_available_at(self, repository, db_session):
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
            error_code="temporary",
            error_message="temporary",
            retry_base_seconds=30,
            max_retries=5,
        )
        db_session.flush()

        recovered = repository.requeue_stale_running_jobs(stale_minutes=GRAPH_INDEX_STALE_MINUTES)
        assert recovered == 0
        assert repository.get(job.id).status == GraphIndexJobStatus.RETRY_WAITING

        job.available_at = naive_utc_now() - timedelta(seconds=1)
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


class TestDispatchableJobs:
    def test_lists_only_available_pending_jobs(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        config = _make_config(tenant_id=tenant_id, dataset_id=dataset_id, graph_version="v1")
        docs = [_make_document(tenant_id=tenant_id, dataset_id=dataset_id) for _ in range(3)]
        db_session.add(config)
        db_session.add_all(docs)
        db_session.flush()
        jobs = [
            repository.create_if_missing(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=doc.id,
                source_version=compute_source_version(_facts(doc)),
                graph_version="v1",
            )
            for doc in docs
        ]
        db_session.flush()
        jobs[1].available_at = naive_utc_now() + timedelta(minutes=5)
        repository.claim(jobs[2].id)
        db_session.flush()

        dispatchable = repository.list_dispatchable_jobs(limit=10)

        assert [job.id for job in dispatchable] == [jobs[0].id]

    def test_excludes_jobs_for_disabled_or_old_graph_config(self, repository, db_session):
        tenant_id = str(uuid4())
        enabled_dataset_id = str(uuid4())
        disabled_dataset_id = str(uuid4())
        enabled_config = _make_config(
            tenant_id=tenant_id,
            dataset_id=enabled_dataset_id,
            graph_version="v2",
            enabled=True,
        )
        disabled_config = _make_config(
            tenant_id=tenant_id,
            dataset_id=disabled_dataset_id,
            graph_version="v1",
            enabled=False,
        )
        current_doc = _make_document(tenant_id=tenant_id, dataset_id=enabled_dataset_id)
        old_doc = _make_document(tenant_id=tenant_id, dataset_id=enabled_dataset_id)
        disabled_doc = _make_document(tenant_id=tenant_id, dataset_id=disabled_dataset_id)
        db_session.add_all([enabled_config, disabled_config, current_doc, old_doc, disabled_doc])
        db_session.flush()

        current_job = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=enabled_dataset_id,
            document_id=current_doc.id,
            source_version=compute_source_version(_facts(current_doc)),
            graph_version="v2",
        )
        repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=enabled_dataset_id,
            document_id=old_doc.id,
            source_version=compute_source_version(_facts(old_doc)),
            graph_version="v1",
        )
        repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=disabled_dataset_id,
            document_id=disabled_doc.id,
            source_version=compute_source_version(_facts(disabled_doc)),
            graph_version="v1",
        )
        db_session.flush()

        dispatchable = repository.list_dispatchable_jobs(limit=10)

        assert [job.id for job in dispatchable] == [current_job.id]

    def test_node_jobs_require_matching_published_scope(self, repository, db_session, monkeypatch):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        document = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add(document)
        db_session.flush()
        source_version = compute_source_version(_facts(document))
        current = repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version=source_version,
            graph_version="node-v2",
            index_node_id="node-a",
        )
        repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version="older-source",
            graph_version="node-v1",
            index_node_id="node-a",
        )
        repository.create_if_missing(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document.id,
            source_version="other-source",
            graph_version="node-v2",
            index_node_id="node-b",
        )
        assert current is not None
        monkeypatch.setattr(
            "core.rag.graph_indexing.repositories.list_published_node_graph_scopes",
            lambda _session: [
                _node_scope(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    index_node_id="node-a",
                    graph_version="node-v2",
                )
            ],
        )

        dispatchable = repository.list_dispatchable_jobs(limit=10)

        assert [job.id for job in dispatchable] == [current.id]


class TestTenantIsolation:
    def test_list_completed_documents_scoped_by_tenant(self, repository, db_session):
        tenant_a, tenant_b = str(uuid4()), str(uuid4())
        dataset_a, dataset_b = str(uuid4()), str(uuid4())
        doc_a = _make_document(tenant_id=tenant_a, dataset_id=dataset_a)
        doc_b = _make_document(tenant_id=tenant_b, dataset_id=dataset_b)
        db_session.add_all([doc_a, doc_b])
        db_session.flush()
        db_session.add_all(
            [
                _make_segment(tenant_id=tenant_a, dataset_id=dataset_a, document_id=doc_a.id),
                _make_segment(tenant_id=tenant_b, dataset_id=dataset_b, document_id=doc_b.id),
            ]
        )
        db_session.flush()

        docs_a = repository.list_completed_documents(tenant_id=tenant_a, dataset_id=dataset_a, limit=10, offset=0)
        assert [d.id for d in docs_a] == [doc_a.id]

    def test_document_and_dataset_scopes_select_disjoint_segments(self, repository, db_session):
        tenant_id, dataset_id = str(uuid4()), str(uuid4())
        node_document = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        fallback_document = _make_document(tenant_id=tenant_id, dataset_id=dataset_id)
        db_session.add_all([node_document, fallback_document])
        db_session.flush()
        node_segment = _make_segment(tenant_id=tenant_id, dataset_id=dataset_id, document_id=node_document.id)
        node_segment.index_node_id = "node-a"
        fallback_segment = _make_segment(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=fallback_document.id,
        )
        db_session.add_all([node_segment, fallback_segment])
        db_session.flush()

        node_documents = repository.list_completed_documents(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            limit=10,
            offset=0,
            index_node_id="node-a",
        )
        fallback_documents = repository.list_completed_documents(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            limit=10,
            offset=0,
            index_node_id=DATASET_GRAPH_INDEX_SCOPE,
            excluded_index_node_ids={"node-a"},
        )

        assert node_documents == [node_document]
        assert fallback_documents == [fallback_document]


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
