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
        assert socket.sent[-1]["cmd"] == "aibot_respond_msg"
        assert socket.sent[-1]["headers"] == {"req_id": "callback-1"}
        assert socket.sent[-1]["body"]["msgtype"] == "stream"
        assert socket.sent[-1]["body"]["stream"]["finish"] is True
        assert socket.sent[-1]["body"]["stream"]["content"] == "hi"
        assert socket.sent[-1]["body"]["stream"]["id"]

    asyncio.run(run())


def test_event_callback_is_ignored_before_text_message() -> None:
    from core.wecom_long_link.protocol import WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {
                    "cmd": "aibot_event_callback",
                    "headers": {"req_id": "event-1"},
                    "body": {
                        "msgid": "event-message-1",
                        "aibotid": "bot-1",
                        "msgtype": "event",
                        "event": {"eventtype": "enter_chat"},
                    },
                },
                {
                    "cmd": "aibot_msg_callback",
                    "headers": {"req_id": "callback-1"},
                    "body": {
                        "msgid": "message-1",
                        "aibotid": "bot-1",
                        "chattype": "single",
                        "from": {"userid": "user-1"},
                        "msgtype": "text",
                        "text": {"content": "hello"},
                    },
                },
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        message = await client.receive_message()

        assert message.message_id == "message-1"
        await client.close()

    asyncio.run(run())


def test_message_callback_does_not_satisfy_reply_response() -> None:
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
                        "chattype": "single",
                        "from": {"userid": "user-1"},
                        "msgtype": "text",
                        "text": {"content": "hello"},
                    },
                },
                {"headers": {"req_id": "callback-1"}, "errcode": 0, "errmsg": "ok", "cmd": ""},
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        message = await client.receive_message()
        await client.respond_text(message, "hi")

        assert socket.sent[-1]["cmd"] == "aibot_respond_msg"
        assert socket.sent[-1]["headers"] == {"req_id": "callback-1"}
        assert socket.sent[-1]["body"]["msgtype"] == "stream"
        assert socket.sent[-1]["body"]["stream"]["finish"] is True
        assert socket.sent[-1]["body"]["stream"]["content"] == "hi"
        assert socket.sent[-1]["body"]["stream"]["id"]
        await client.close()

    asyncio.run(run())


def test_reader_error_is_delivered_to_message_receiver() -> None:
    from core.wecom_long_link.protocol import WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(sent=[], incoming=[])
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        with pytest.raises(RuntimeError, match="fake websocket exhausted"):
            await client.receive_message()

        await client.close()

    asyncio.run(run())


def test_stream_update_uses_shared_stream_id() -> None:
    from core.wecom_long_link.protocol import WeComMessage, WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {"headers": {"req_id": "callback-1"}, "errcode": 0, "errmsg": "ok"},
                {"headers": {"req_id": "callback-1"}, "errcode": 0, "errmsg": "ok"},
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")
        message = WeComMessage(
            message_id="message-1",
            request_id="callback-1",
            bot_id="bot-1",
            chat_id=None,
            chat_type=None,
            user_id="user-1",
            message_type="text",
            text="hello",
            raw_body={},
        )

        await client.respond_stream(message, "节点已完成", stream_id="stream-1", finish=False)

        assert [item["body"]["stream"] for item in socket.sent] == [
            {"id": "stream-1", "finish": False, "content": "节点已完成"},
        ]
        await client.close()

    asyncio.run(run())

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[{"headers": {"req_id": "callback-1"}, "errcode": 0, "errmsg": "ok"}],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")
        message = WeComMessage(
            message_id="message-1",
            request_id="callback-1",
            bot_id="bot-1",
            chat_id=None,
            chat_type=None,
            user_id="user-1",
            message_type="text",
            text="hello",
            raw_body={},
        )

        await client.respond_text(message, "hi", stream_id="stream-1")

        assert socket.sent[-1]["body"]["stream"]["id"] == "stream-1"
        await client.close()

    asyncio.run(run())

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[
                {"headers": {"req_id": "callback-1"}, "errcode": 0, "errmsg": "ok", "cmd": ""},
                {"headers": {"req_id": "ping-1"}, "errcode": 0, "errmsg": "ok"},
            ],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")
        message = WeComMessage(
            message_id="message-1",
            request_id="callback-1",
            bot_id="bot-1",
            chat_id="chat-1",
            chat_type="single",
            user_id="user-1",
            message_type="text",
            text="hello",
            raw_body={},
        )

        await client.respond_text(message, "hi")
        await client.ping(req_id="ping-1")

        assert socket.sent[0]["cmd"] == "aibot_respond_msg"
        assert socket.sent[0]["headers"] == {"req_id": "callback-1"}
        assert socket.sent[0]["body"]["msgtype"] == "stream"
        assert socket.sent[0]["body"]["stream"]["finish"] is True
        assert socket.sent[0]["body"]["stream"]["content"] == "hi"
        assert socket.sent[0]["body"]["stream"]["id"]
        assert socket.sent[1] == {"cmd": "ping", "headers": {"req_id": "ping-1"}}
        await client.close()

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


def test_response_without_command_is_accepted_for_pending_request() -> None:
    from core.wecom_long_link.protocol import WeComWebSocketProtocol

    async def run() -> None:
        socket = FakeWebSocket(
            sent=[],
            incoming=[{"headers": {"req_id": "ping-1"}, "errcode": 0, "errmsg": "ok"}],
        )
        client = WeComWebSocketProtocol(socket=socket, bot_id="bot-1", secret="secret-1")

        await client.ping(req_id="ping-1")
        await client.close()

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
