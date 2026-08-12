from __future__ import annotations

import asyncio

import pytest

from core.wecom_long_link.client import WeComOutboundClient
from core.wecom_long_link.config import WeComBotConfig
from core.wecom_long_link.protocol import WeComProtocolError


class _Store:
    def __init__(self, *, renew_result=True):
        self.renew_result = renew_result
        self.reset_calls = 0

    def acquire(self, key, owner, ttl):
        return True

    def renew(self, key, owner, ttl):
        return self.renew_result

    def release(self, key, owner):
        return None

    def get(self, key):
        return None

    def claim(self, key):
        return True

    def reset_processing(self, key):
        self.reset_calls += 1

    def mark_completed(self, key, content):
        return None

    def mark_reply_sent(self, key):
        return None


class _Protocol:
    def __init__(self, *, failure=None):
        self.failure = failure
        self.closed = asyncio.Event()
        self.handler_started = asyncio.Event()
        self.handler_cancelled = asyncio.Event()

    async def subscribe(self, req_id):
        return None

    async def receive_message(self):
        await asyncio.Event().wait()

    async def wait_closed(self):
        await self.closed.wait()
        if self.failure:
            raise self.failure

    async def respond_stream(self, *args, **kwargs):
        return None

    async def close(self):
        self.closed.set()


@pytest.fixture
def config():
    return WeComBotConfig("tenant", "instance", "bot", "secret", "app", True, "v1")


def test_client_resets_processing_and_closes_protocol_when_lease_renewal_fails(config):
    async def run():
        protocol = _Protocol()
        store = _Store()
        client = WeComOutboundClient(config, socket_factory=lambda _: None, state_store=store)
        import core.wecom_long_link.client as client_module
        original_protocol = client_module.WeComWebSocketProtocol
        client_module.WeComWebSocketProtocol = lambda **kwargs: protocol
        original_renew = client._renew_lease

        async def fail_renewal(key, owner, stop_event):
            await asyncio.sleep(0)
            raise RuntimeError("lease renewal failed")

        client._renew_lease = fail_renewal

        async def on_message(*args):
            protocol.handler_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                protocol.handler_cancelled.set()
                raise

        async def close():
            protocol.closed.set()

        protocol.close = close
        try:
            task = asyncio.create_task(client.run(on_message=on_message))
            await protocol.handler_started.wait()
            while store.reset_calls == 0:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            client_module.WeComWebSocketProtocol = original_protocol
            client._renew_lease = original_renew

        assert protocol.handler_cancelled.is_set()
        assert store.reset_calls == 1

    asyncio.run(run())


def test_client_cancels_message_handler_when_lease_is_lost(config):
    async def run():
        protocol = _Protocol()
        client = WeComOutboundClient(config, socket_factory=lambda _: None, state_store=_Store())
        original_protocol = __import__(
            "core.wecom_long_link.client", fromlist=["WeComWebSocketProtocol"]
        ).WeComWebSocketProtocol
        try:
            import core.wecom_long_link.client as client_module
            client_module.WeComWebSocketProtocol = lambda **kwargs: protocol
            client._renew_lease = lambda key, owner, stop: asyncio.sleep(0.01, result=None)

            async def on_message(*args):
                protocol.handler_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    protocol.handler_cancelled.set()
                    raise

            task = asyncio.create_task(client.run(on_message=on_message))
            await protocol.handler_started.wait()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert protocol.handler_cancelled.is_set()
        finally:
            client_module.WeComWebSocketProtocol = original_protocol

    asyncio.run(run())


def test_client_cancels_message_handler_when_connection_closes(config):
    async def run():
        protocol = _Protocol(failure=WeComProtocolError("connection closed"))
        client = WeComOutboundClient(config, socket_factory=lambda _: None, state_store=_Store())
        import core.wecom_long_link.client as client_module
        original_protocol = client_module.WeComWebSocketProtocol
        client_module.WeComWebSocketProtocol = lambda **kwargs: protocol
        try:
            async def on_message(*args):
                protocol.handler_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    protocol.handler_cancelled.set()
                    raise

            task = asyncio.create_task(client.run(on_message=on_message))
            await protocol.handler_started.wait()
            protocol.closed.set()
            with pytest.raises(WeComProtocolError, match="connection closed"):
                await task
            assert protocol.handler_cancelled.is_set()
        finally:
            client_module.WeComWebSocketProtocol = original_protocol

    asyncio.run(run())
