from __future__ import annotations

from dataclasses import dataclass

from core.wecom_long_link.feedback import FeedbackCoordinator, FeedbackFrame


@dataclass
class FakeClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_feedback_coordinator_keeps_one_stream_and_appends_final_frame() -> None:
    clock = FakeClock()
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append, clock=clock)

    coordinator.on_node_change("检索节点")
    clock.advance(5)
    coordinator.advance()
    coordinator.on_node_change("回答节点")
    clock.advance(4)
    coordinator.advance()
    clock.advance(1)
    coordinator.advance()
    coordinator.on_final_text("最终答案")
    coordinator.on_final_text("最终答案")
    coordinator.advance()

    assert frames == [
        FeedbackFrame("stream-1", "检索节点…｜已用 0 秒", False),
        FeedbackFrame("stream-1", "检索节点…｜已用 5 秒", False),
        FeedbackFrame("stream-1", "回答节点…｜已用 10 秒", False),
        FeedbackFrame("stream-1", "最终答案", True),
    ]
    assert {frame.stream_id for frame in frames} == {"stream-1"}
    assert sum(frame.finish for frame in frames) == 1


def test_feedback_coordinator_defers_dense_node_updates_until_stream_window() -> None:
    clock = FakeClock()
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append, clock=clock)

    coordinator.on_node_change("节点一")
    clock.advance(2)
    coordinator.on_node_change("节点二")
    coordinator.advance()
    clock.advance(2)
    coordinator.on_node_change("节点三")
    coordinator.advance()
    clock.advance(1)
    coordinator.advance()

    assert frames == [
        FeedbackFrame("stream-1", "节点一…｜已用 0 秒", False),
        FeedbackFrame("stream-1", "节点三…｜已用 5 秒", False),
    ]


def test_feedback_coordinator_uses_stable_fallback_for_empty_node() -> None:
    clock = FakeClock()
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append, clock=clock)

    coordinator.on_node_change(None)

    assert frames == [FeedbackFrame("stream-1", "处理中…｜已用 0 秒", False)]


def test_feedback_coordinator_appends_answer_chunks_and_finishes_once() -> None:
    clock = FakeClock()
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append, clock=clock)

    coordinator.on_node_change("节点")
    coordinator.on_answer_chunk("第一段")
    coordinator.on_answer_chunk("第二段")
    coordinator.on_final_text("第一段第二段")
    coordinator.on_final_text("第一段第二段")

    assert frames == [
        FeedbackFrame("stream-1", "节点…｜已用 0 秒", False),
        FeedbackFrame("stream-1", "第一段", False),
        FeedbackFrame("stream-1", "第二段", True),
    ]


def test_feedback_coordinator_throttles_answer_chunks_and_flushes_pending_text() -> None:
    clock = FakeClock()
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append, clock=clock)

    coordinator.on_answer_chunk("第一段")
    coordinator.on_answer_chunk("第二段")
    clock.advance(5)
    coordinator.on_answer_chunk("第三段")
    coordinator.on_final_text("第一段第二段第三段")

    assert frames == [
        FeedbackFrame("stream-1", "第一段", False),
        FeedbackFrame("stream-1", "第二段第三段", True),
    ]


def test_feedback_coordinator_stops_after_disconnect() -> None:
    clock = FakeClock()
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append, clock=clock)

    coordinator.on_node_change("节点")
    coordinator.on_disconnected()
    clock.advance(10)
    coordinator.advance()
    coordinator.on_final_text("答案")

    assert frames == [FeedbackFrame("stream-1", "节点…｜已用 0 秒", False)]


def test_feedback_coordinator_replace_keeps_previous_answer_out_of_final_frame() -> None:
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append)

    coordinator.on_answer_replace("完整答案")
    coordinator.on_final_text("完整答案")

    assert frames == [FeedbackFrame("stream-1", "完整答案", False), FeedbackFrame("stream-1", "完整答案", True)]
    frames: list[FeedbackFrame] = []
    coordinator = FeedbackCoordinator(stream_id="stream-1", send=frames.append)

    coordinator.on_final_text("")
    coordinator.on_final_text("答案")
    coordinator.on_final_text("答案")

    assert frames == [FeedbackFrame("stream-1", "答案", True)]
