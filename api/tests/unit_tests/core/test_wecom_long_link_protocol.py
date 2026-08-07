from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest


@dataclass
class FakeWebSocket:
    sent: list[dict[str, Any]]
    incoming: list[dict[str, Any]]
    closed: bool = False

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if not self.incoming:
            raise RuntimeError("fake websocket exhausted")
        return json.dumps(self.incoming.pop(0))

    async def close(self) -> None:
        self.closed = True


def test_subscribe_uses_bot_credentials_and_correlates_request() -> None:
    from core.wecom_long_link.protocol import WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {"headers": {"req_id": "subscribe-1"}, "errcode": 0, "errmsg": "ok", "cmd": "aibot_subscribe"}
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        await client.subscribe(req_id="subscribe-1")

        assert socket.sent == [
            {
                "cmd": "aibot_subscribe",
                "headers": {"req_id": "subscribe-1"},
                "body": {"bot_id": "bot-1", "secret": "secret-1"},
            }
        ]

    asyncio.run(run())


def test_message_callback_is_normalized_and_reply_keeps_req_id() -> None:
    from core.wecom_long_link.protocol import WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {
                    "cmd": "aibot_msg_callback",
                    "headers": {"req_id": "callback-1"},
                    "body": {
                        "msgid": "message-1",
                        "aibotid": "bot-1",
                        "chatid": "chat-1",
                        "chattype": "single",
                        "from": {"userid": "user-1"},
                        "msgtype": "text",
                        "text": {"content": "hello"},
                    },
                },
                {"headers": {"req_id": "callback-1"}, "errcode": 0, "errmsg": "ok", "cmd": "aibot_respond_msg"},
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        message = await client.receive_message()
        await client.respond_text(message, "hi")

        assert message.message_id == "message-1"
        assert message.request_id == "callback-1"
        assert message.text == "hello"
        assert socket.sent[-1] == {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": "callback-1"},
            "body": {"msgtype": "text", "text": {"content": "hi"}},
        }

    asyncio.run(run())


def test_application_ping_uses_request_id() -> None:
    from core.wecom_long_link.protocol import WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[{"headers": {"req_id": "ping-1"}, "errcode": 0, "errmsg": "ok", "cmd": "ping"}],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        await client.ping(req_id="ping-1")

        assert socket.sent == [{"cmd": "ping", "headers": {"req_id": "ping-1"}}]

    asyncio.run(run())


def test_missing_callback_bot_id_is_rejected() -> None:
    from core.wecom_long_link.protocol import WeComMalformedFrameError, WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {
                    "cmd": "aibot_msg_callback",
                    "headers": {"req_id": "callback-1"},
                    "body": {
                        "msgid": "message-1",
                        "chatid": "chat-1",
                        "chattype": "single",
                        "from": {"userid": "user-1"},
                        "msgtype": "text",
                        "text": {"content": "hello"},
                    },
                }
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        with pytest.raises(WeComMalformedFrameError, match="missing aibotid"):
            await client.receive_message()

    asyncio.run(run())


def test_missing_response_command_is_rejected() -> None:
    from core.wecom_long_link.protocol import WeComResponseError, WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[{"headers": {"req_id": "ping-1"}, "errcode": 0, "errmsg": "ok"}],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        with pytest.raises(WeComResponseError, match="command mismatch"):
            await client.ping(req_id="ping-1")

    asyncio.run(run())


def test_response_command_must_match_operation() -> None:
    from core.wecom_long_link.protocol import WeComResponseError, WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {"cmd": "aibot_respond_msg", "headers": {"req_id": "subscribe-1"}, "errcode": 0, "errmsg": "ok"}
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        with pytest.raises(WeComResponseError, match="command mismatch"):
            await client.subscribe(req_id="subscribe-1")

    asyncio.run(run())

    from core.wecom_long_link.protocol import WeComProtocolError

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {
                    "cmd": "aibot_subscribe",
                    "headers": {"req_id": "subscribe-1"},
                    "errcode": 40001,
                    "errmsg": "invalid secret",
                }
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        with pytest.raises(WeComProtocolError, match="subscribe failed") as exc_info:
            await client.subscribe(req_id="subscribe-1")

        assert "secret-1" not in str(exc_info.value)

    asyncio.run(run())
