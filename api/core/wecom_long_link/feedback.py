from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class FeedbackFrame:
    stream_id: str
    content: str
    finish: bool


class FeedbackCoordinator:
    """协调同一企业微信 stream 的节点状态与最终答案帧。"""

    MIN_UPDATE_INTERVAL = 5.0
    FALLBACK_NODE = "处理中"

    def __init__(
        self,
        *,
        stream_id: str,
        send: Callable[[FeedbackFrame], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stream_id = stream_id
        self._send = send
        self._clock = clock
        self._started_at = clock()
        self._last_sent_at: float | None = None
        self._current_node = self.FALLBACK_NODE
        self._pending = True
        self._completed = False
        self._answer = ""
        self._pending_answer = ""
        self._last_answer_sent_at: float | None = None
        self._answer_started = False

    def on_node_change(self, node_name: str | None) -> None:
        if self._completed or self._answer_started:
            return
        self._pending = True
        self._current_node = (
            node_name.strip() if isinstance(node_name, str) and node_name.strip() else self.FALLBACK_NODE
        )
        self._pending = True
        self.advance()

    def advance(self) -> None:
        if self._completed:
            return
        now = self._clock()
        if self._last_sent_at is not None and now - self._last_sent_at < self.MIN_UPDATE_INTERVAL:
            return
        if self._last_sent_at is None and not self._pending:
            return
        elapsed_seconds = int(now - self._started_at)
        self._send(FeedbackFrame(self.stream_id, f"{self._current_node}…｜已用 {elapsed_seconds} 秒", False))
        self._last_sent_at = now
        self._pending = False

    def on_answer_chunk(self, chunk: str) -> None:
        if self._completed or not isinstance(chunk, str) or not chunk:
            return
        self._answer += chunk
        self._answer_started = True
        self._pending_answer += chunk
        self._flush_answer(force=False, finish=False)

    def on_answer_replace(self, text: str) -> None:
        if self._completed or not isinstance(text, str) or not text:
            return
        self._answer = text
        self._answer_started = True
        self._pending_answer = text
        self._flush_answer(force=True, finish=False)

    def on_final_text(self, text: str) -> None:
        """发送最终答案的未发送部分，并保证完成帧只发送一次。"""
        if self._completed or not isinstance(text, str) or not text:
            return
        self._answer_started = True
        if text.startswith(self._answer):
            self._pending_answer += text[len(self._answer) :]
        else:
            self._pending_answer = text
        if not self._pending_answer:
            self._pending_answer = text
        self._answer = text
        self._flush_answer(force=True, finish=True)
        self._completed = True
        self._pending = False

    def on_disconnected(self) -> None:
        """停止断流后的定时更新，不改变已发送内容。"""
        self._pending = False
        self._completed = True

    def on_cancelled(self) -> None:
        """停止取消后的所有反馈发送。"""
        self.on_disconnected()

    def _flush_answer(self, *, force: bool, finish: bool) -> None:
        if not self._pending_answer:
            return
        now = self._clock()
        if not force and self._last_answer_sent_at is not None:
            if now - self._last_answer_sent_at <= self.MIN_UPDATE_INTERVAL:
                return
        self._send(FeedbackFrame(self.stream_id, self._pending_answer, finish))
        self._last_answer_sent_at = now
        self._pending_answer = ""
