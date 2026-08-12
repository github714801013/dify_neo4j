from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.wecom_long_link import worker
from core.wecom_long_link.config import WeComBotConfig


class _EventStream:
    def __init__(self, events):
        self._events = iter(events)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._events)

    def close(self):
        self.closed = True


class _Router:
    def __init__(self, events):
        self.events = events
        self.stream = None

    def stream_reply(self, **kwargs):
        self.stream = _EventStream(self.events)
        return self.stream


class _Protocol:
    def __init__(self, *, failure=None, fail_on=None):
        self.frames = []
        self.attempts = []
        self.failure = failure
        self.fail_on = fail_on

    async def respond_stream(self, message, content, *, stream_id, finish):
        self.attempts.append((content, finish))
        if self.failure and (self.fail_on is None or len(self.attempts) == self.fail_on):
            raise self.failure
        self.frames.append((content, finish))


class _Reconciler:
    instance = None

    def __init__(self, provider, client_factory):
        self.on_message = None
        _Reconciler.instance = self

    async def reconcile(self, *, on_message=None):
        self.on_message = on_message
        await asyncio.Event().wait()

    async def stop(self):
        return None


def _message():
    return SimpleNamespace(bot_id="bot", message_id="msg", user_id="user", text="hello", chat_id=None)


def _config():
    return WeComBotConfig("tenant", "instance", "bot", "secret", "app", True, "v1")


async def _start_callback(monkeypatch, router):
    monkeypatch.setattr(worker, "build_config_provider", lambda tenant, user: lambda: [_config()])
    monkeypatch.setattr(worker, "WeComAppRouter", lambda: router)
    monkeypatch.setattr(worker, "WeComConfigReconciler", _Reconciler)
    task = asyncio.create_task(worker._run_tenant("tenant"))
    while _Reconciler.instance is None or _Reconciler.instance.on_message is None:
        await asyncio.sleep(0)
    return task, _Reconciler.instance.on_message


@pytest.mark.asyncio
async def test_worker_reads_stream_sends_frames_and_closes_stream(monkeypatch):
    router = _Router(
        [
            SimpleNamespace(kind="node_finished", node_title="节点", content="节点…"),
            SimpleNamespace(kind="answer_chunk", content="答案"),
            SimpleNamespace(kind="final", content="答案"),
        ]
    )
    protocol = _Protocol()
    task, callback = await _start_callback(monkeypatch, router)

    try:
        result = await callback(_message(), protocol, "stream")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert result == "答案"
    assert protocol.frames[-1] == ("答案", True)
    assert router.stream.closed


@pytest.mark.asyncio
async def test_worker_stops_timer_after_stream_finishes(monkeypatch):
    router = _Router([SimpleNamespace(kind="final", content="答案")])
    protocol = _Protocol()
    task, callback = await _start_callback(monkeypatch, router)

    try:
        await callback(_message(), protocol, "stream")
        frame_count = len(protocol.frames)
        await asyncio.sleep(1.1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(protocol.frames) == frame_count


@pytest.mark.asyncio
async def test_worker_stops_stream_and_sends_no_more_frames_after_respond_failure(monkeypatch):
    router = _Router(
        [
            SimpleNamespace(kind="node_finished", node_title="节点", content="节点…"),
            SimpleNamespace(kind="answer_chunk", content="答案"),
            SimpleNamespace(kind="final", content="答案"),
        ]
    )
    protocol = _Protocol(failure=RuntimeError("respond failed"), fail_on=2)
    task, callback = await _start_callback(monkeypatch, router)

    try:
        with pytest.raises(RuntimeError, match="respond failed"):
            await callback(_message(), protocol, "stream")
        attempts = len(protocol.attempts)
        await asyncio.sleep(1.1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert attempts >= 2
    assert len(protocol.attempts) == attempts
    assert router.stream.closed


@pytest.mark.asyncio
async def test_worker_closes_stream_when_callback_is_cancelled(monkeypatch):
    router = _Router([SimpleNamespace(kind="final", content="答案")])
    protocol = _Protocol()
    task, callback = await _start_callback(monkeypatch, router)

    callback_task = asyncio.create_task(callback(_message(), protocol, "stream"))
    callback_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await callback_task
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert router.stream is None or router.stream.closed
