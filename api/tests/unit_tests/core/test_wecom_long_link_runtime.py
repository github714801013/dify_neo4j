from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.wecom_long_link.app_router import WeComAppRouter
from core.wecom_long_link.config import WeComBotConfig
from core.wecom_long_link.reconciler import WeComConfigReconciler
from models.model import AppMode


class _FakeClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def run(self, *, on_message=None) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.stopped.set()
            raise


def _config(version: str, enabled: bool = True) -> WeComBotConfig:
    return WeComBotConfig("tenant-1", "instance-1", "bot-1", "secret-1", "app-1", enabled, version)


def test_reconciler_starts_and_replaces_client() -> None:
    async def run() -> None:
        configs = [_config("v1")]
        clients: list[_FakeClient] = []

        def factory(_config):
            client = _FakeClient()
            clients.append(client)
            return client

        reconciler = WeComConfigReconciler(lambda: configs, factory)
        await reconciler.reconcile()
        await clients[0].started.wait()
        configs[:] = [_config("v2")]
        await reconciler.reconcile()
        await clients[1].started.wait()
        assert clients[0].stopped.is_set()
        assert reconciler._running["instance-1"].version == "v2"
        await reconciler.stop()

    asyncio.run(run())


def test_reconciler_stops_disabled_client() -> None:
    async def run() -> None:
        configs = [_config("v1")]
        clients: list[_FakeClient] = []
        reconciler = WeComConfigReconciler(lambda: configs, lambda _: clients.append(_FakeClient()) or clients[-1])
        await reconciler.reconcile()
        await clients[0].started.wait()
        configs[:] = [_config("v1", enabled=False)]
        await reconciler.reconcile()
        assert clients[0].stopped.is_set()
        assert not reconciler._running
        await reconciler.stop()

    asyncio.run(run())


def test_app_router_creates_safe_node_summary() -> None:
    event = SimpleNamespace(
        workflow_run_id="workflow-run-1",
        data=SimpleNamespace(
            id="execution-1",
            node_id="node-1",
            node_type="llm",
            title="分析节点",
            elapsed_time=1.2,
        ),
    )

    summary = WeComAppRouter._node_summary(event, set())

    assert summary is not None
    assert summary.kind == "node_finished"
    assert summary.content == "节点已完成：分析节点（llm），耗时 1.2 秒"
    assert summary.workflow_run_id == "workflow-run-1"


def test_app_router_deduplicates_node_summary() -> None:
    event = SimpleNamespace(
        workflow_run_id="workflow-run-1",
        data=SimpleNamespace(
            id="execution-1",
            node_id="node-1",
            node_type="llm",
            title="分析节点",
            elapsed_time=1.2,
        ),
    )
    seen_nodes: set[tuple[str | None, str | None]] = set()

    assert WeComAppRouter._node_summary(event, seen_nodes) is not None
    assert WeComAppRouter._node_summary(event, seen_nodes) is None


def test_app_router_rejects_cross_tenant_app(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, _model, _app_id):
            return SimpleNamespace(tenant_id="tenant-other", mode=AppMode.CHAT)

    monkeypatch.setattr("core.wecom_long_link.app_router.create_session", lambda: Session())
    router = WeComAppRouter()
    with pytest.raises(ValueError, match="does not exist"):
        router.generate_reply(tenant_id="tenant-1", app_id="app-1", user_id="user-1", query="hello")
