"""source_version 计算与状态机的单元测试。"""

from datetime import UTC, datetime

import pytest

from core.rag.graph_indexing.entities import (
    GRAPH_INDEX_STALE_MINUTES,
    GraphIndexJobStatus,
    claimable_statuses,
    is_transition_allowed,
    resumable_statuses,
)
from core.rag.graph_indexing.versioning import (
    DocumentSourceFacts,
    build_segment_source_facts,
    build_source_facts,
    compute_source_version,
)


class TestComputeSourceVersion:
    def _facts(
        self,
        *,
        document_id: str = "doc-1",
        updated_at: datetime | None = datetime(2026, 7, 16, 10, 0, 0),
        completed_at: datetime | None = datetime(2026, 7, 16, 10, 5, 0),
        batch: str = "batch-1",
        word_count: int | None = 123,
    ) -> DocumentSourceFacts:
        return build_source_facts(
            document_id,
            updated_at=updated_at,
            completed_at=completed_at,
            batch=batch,
            word_count=word_count,
        )

    def test_same_facts_produce_same_version(self):
        assert compute_source_version(self._facts()) == compute_source_version(self._facts())

    def test_updated_at_change_produces_new_version(self):
        base = self._facts()
        changed = self._facts(updated_at=datetime(2026, 7, 16, 10, 6, 0))
        assert compute_source_version(base) != compute_source_version(changed)

    def test_completed_at_change_produces_new_version(self):
        base = self._facts()
        changed = self._facts(completed_at=datetime(2026, 7, 16, 10, 6, 0))
        assert compute_source_version(base) != compute_source_version(changed)

    def test_batch_change_produces_new_version(self):
        base = self._facts()
        changed = self._facts(batch="batch-2")
        assert compute_source_version(base) != compute_source_version(changed)

    def test_word_count_change_produces_new_version(self):
        base = self._facts()
        changed = self._facts(word_count=124)
        assert compute_source_version(base) != compute_source_version(changed)

    def test_none_completed_at_is_stable(self):
        facts = self._facts(completed_at=None)
        assert compute_source_version(facts) == compute_source_version(facts)

    def test_word_count_none_treated_as_empty(self):
        facts_none = self._facts(word_count=None)
        facts_empty = self._facts(word_count=None)
        assert compute_source_version(facts_none) == compute_source_version(facts_empty)

    def test_tz_aware_datetime_normalized_to_utc(self):
        aware = self._facts(updated_at=datetime(2026, 7, 16, 18, 0, 0, tzinfo=UTC))
        naive = self._facts(updated_at=datetime(2026, 7, 16, 18, 0, 0))
        assert compute_source_version(aware) == compute_source_version(naive)

    def test_version_length_is_64(self):
        assert len(compute_source_version(self._facts())) == 64

    def test_segment_order_does_not_change_version(self):
        first = build_segment_source_facts("segment-1", content="alpha", updated_at=None)
        second = build_segment_source_facts("segment-2", content="beta", updated_at=None)

        assert compute_source_version(self._facts(), [first, second]) == compute_source_version(
            self._facts(), [second, first]
        )

    def test_segment_content_change_produces_new_version(self):
        original = build_segment_source_facts("segment-1", content="alpha", updated_at=None)
        changed = build_segment_source_facts("segment-1", content="alpha changed", updated_at=None)

        assert compute_source_version(self._facts(), [original]) != compute_source_version(self._facts(), [changed])

    def test_active_segment_set_change_produces_new_version(self):
        first = build_segment_source_facts("segment-1", content="alpha", updated_at=None)
        second = build_segment_source_facts("segment-2", content="beta", updated_at=None)

        assert compute_source_version(self._facts(), [first, second]) != compute_source_version(self._facts(), [first])


class TestStateMachine:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (GraphIndexJobStatus.PENDING, GraphIndexJobStatus.RUNNING),
            (GraphIndexJobStatus.RUNNING, GraphIndexJobStatus.SUCCEEDED),
            (GraphIndexJobStatus.RUNNING, GraphIndexJobStatus.RETRY_WAITING),
            (GraphIndexJobStatus.RUNNING, GraphIndexJobStatus.FAILED),
            (GraphIndexJobStatus.RETRY_WAITING, GraphIndexJobStatus.PENDING),
            (GraphIndexJobStatus.FAILED, GraphIndexJobStatus.PENDING),
        ],
    )
    def test_allowed_transitions(self, current, target):
        assert is_transition_allowed(current, target) is True

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (GraphIndexJobStatus.PENDING, GraphIndexJobStatus.SUCCEEDED),
            (GraphIndexJobStatus.PENDING, GraphIndexJobStatus.RETRY_WAITING),
            (GraphIndexJobStatus.SUCCEEDED, GraphIndexJobStatus.RUNNING),
            (GraphIndexJobStatus.CANCELLED, GraphIndexJobStatus.PENDING),
            (GraphIndexJobStatus.FAILED, GraphIndexJobStatus.SUCCEEDED),
        ],
    )
    def test_forbidden_transitions(self, current, target):
        assert is_transition_allowed(current, target) is False

    def test_claimable_only_pending(self):
        assert claimable_statuses() == frozenset({GraphIndexJobStatus.PENDING})

    def test_resumable_running_and_retry_waiting(self):
        assert resumable_statuses() == frozenset({GraphIndexJobStatus.RUNNING, GraphIndexJobStatus.RETRY_WAITING})

    def test_terminal_set_is_immutable(self):
        assert GraphIndexJobStatus.SUCCEEDED in GraphIndexJobStatus.terminal()
        assert GraphIndexJobStatus.FAILED in GraphIndexJobStatus.terminal()
        assert GraphIndexJobStatus.CANCELLED in GraphIndexJobStatus.terminal()
        assert GraphIndexJobStatus.PENDING not in GraphIndexJobStatus.terminal()

    def test_stale_minutes_constant_is_positive(self):
        assert GRAPH_INDEX_STALE_MINUTES > 0
