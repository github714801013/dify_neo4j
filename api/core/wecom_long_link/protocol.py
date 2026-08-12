from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class WebSocketLike(Protocol):
    async def send(self, payload: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


class WeComProtocolError(RuntimeError):
    """企业微信长连接协议错误。"""


class WeComUnknownCommandError(WeComProtocolError):
    """收到无法识别的服务端命令。"""


class WeComResponseError(WeComProtocolError):
    """收到与当前请求不匹配或失败的响应。"""


class WeComMalformedFrameError(WeComProtocolError):
    """收到结构不符合协议的帧。"""


class WeComAuthenticationError(WeComResponseError):
    """订阅凭据无效。"""


@dataclass(frozen=True)
class WeComMessage:
    message_id: str
    request_id: str
    bot_id: str
    chat_id: str | None
    chat_type: str | None
    user_id: str | None
    message_type: str
    text: str
    raw_body: dict[str, Any]


class WeComWebSocketProtocol:
    """企业微信智能机器人长连接协议适配器。"""

    def __init__(self, *, socket: WebSocketLike, bot_id: str, secret: str) -> None:
        self._require_non_empty_string(bot_id, "bot_id")
        self._require_non_empty_string(secret, "secret")
        self._socket = socket
        self._bot_id = bot_id
        self._secret = secret
        self._request_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._pending_commands: dict[str, str] = {}
        self._orphan_responses: dict[str, dict[str, Any]] = {}
        self._messages: asyncio.Queue[WeComMessage | BaseException] = asyncio.Queue(maxsize=100)
        self._closed = False
        self._reader_error: BaseException | None = None
        self._closed_event = asyncio.Event()

    async def subscribe(self, *, req_id: str) -> None:
        self._require_non_empty_string(req_id, "req_id")
        await self._request(
            {
                "cmd": "aibot_subscribe",
                "headers": {"req_id": req_id},
                "body": {"bot_id": self._bot_id, "secret": self._secret},
            },
            operation="subscribe",
            req_id=req_id,
        )

    async def receive_message(self) -> WeComMessage:
        await self._ensure_reader()
        item = await self._messages.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def wait_closed(self) -> None:
        await self._closed_event.wait()
        if self._reader_error is not None:
            raise self._reader_error
        raise WeComProtocolError("websocket protocol is closed")

    async def respond_text(self, message: WeComMessage, content: str, *, stream_id: str | None = None) -> None:
        await self.respond_stream(message, content, stream_id=stream_id or str(uuid.uuid4()), finish=True)

    async def respond_stream(self, message: WeComMessage, content: str, *, stream_id: str, finish: bool) -> None:
        if not isinstance(content, str) or not content:
            raise ValueError("response content must be non-empty")
        content = self._require_non_empty_string(content, "response content")
        self._require_non_empty_string(stream_id, "response stream id")
        if not isinstance(finish, bool):
            raise TypeError("response stream finish must be a bool")
        await self._request(
            {
                "cmd": "aibot_respond_msg",
                "headers": {"req_id": message.request_id},
                "body": {
                    "msgtype": "stream",
                    "stream": {"id": stream_id, "finish": finish, "content": content},
                },
            },
            operation="respond",
            req_id=message.request_id,
        )

    async def ping(self, *, req_id: str) -> None:
        self._require_non_empty_string(req_id, "req_id")
        await self._request(
            {"cmd": "ping", "headers": {"req_id": req_id}},
            operation="ping",
            req_id=req_id,
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closed_event.set()
        error = WeComProtocolError("websocket protocol is closed")
        self._fail_pending(error)
        self._orphan_responses.clear()
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
            self._reader_task = None
        await self._socket.close()

    async def _request(self, payload: dict[str, Any], *, operation: str, req_id: str) -> None:
        async with self._request_lock:
            await self._ensure_reader()
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending[req_id] = future
            self._pending_commands[req_id] = payload["cmd"]
            try:
                response = self._orphan_responses.pop(req_id, None)
                await self._socket.send(json.dumps(payload, ensure_ascii=False))
                if response is None:
                    response = await asyncio.wait_for(future, timeout=15)
                self._ensure_success(response, operation, req_id=req_id)
            finally:
                self._pending.pop(req_id, None)
                self._pending_commands.pop(req_id, None)

    async def _ensure_reader(self) -> None:
        if self._closed:
            raise WeComProtocolError("websocket protocol is closed")
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_frames())

    async def _read_frames(self) -> None:
        try:
            while not self._closed:
                payload = await self._receive_json()
                command = self._normalize_command(payload.get("cmd"))
                headers = payload.get("headers")
                if not isinstance(headers, dict):
                    raise WeComMalformedFrameError("websocket frame headers must be an object")
                req_id = self._require_non_empty_string(headers.get("req_id"), "websocket req_id")
                logger.info("WeCom websocket frame received command=%s req_id=%s", command, req_id)
                if command == "aibot_event_callback":
                    event = payload.get("body")
                    event_type = event.get("event", {}).get("eventtype") if isinstance(event, dict) else None
                    logger.info("WeCom websocket event received req_id=%s event_type=%s", req_id, event_type)
                    continue
                if command == "aibot_msg_callback":
                    logger.info(
                        "WeCom websocket callback received req_id=%s command=%s",
                        req_id,
                        command,
                    )
                    message = self._parse_message(payload)
                    if message.message_type != "text":
                        raise WeComUnknownCommandError(f"unsupported message type: {message.message_type}")
                    await self._messages.put(message)
                    continue
                if command != "aibot_msg_callback" and command not in {"aibot_subscribe", "aibot_respond_msg", "ping"}:
                    if command is not None:
                        raise WeComUnknownCommandError(f"unknown websocket command: {command!r}")
                if command is None and req_id in self._pending_commands:
                    command = self._pending_commands[req_id]
                future = self._pending.get(req_id)
                if future is None:
                    if len(self._orphan_responses) < 100:
                        self._orphan_responses[req_id] = payload
                    continue
                if not future.done():
                    future.set_result(payload)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not self._closed:
                logger.warning("WeCom websocket reader failed error_type=%s", type(exc).__name__, exc_info=True)
                self._reader_error = exc
                self._closed_event.set()
                self._fail_pending(exc)
                await self._messages.put(exc)

    async def _receive_json(self) -> dict[str, Any]:
        try:
            payload = json.loads(await self._socket.recv())
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WeComMalformedFrameError("invalid websocket response") from exc
        if not isinstance(payload, dict):
            raise WeComMalformedFrameError("websocket response must be an object")
        return payload

    def _parse_message(self, payload: dict[str, Any]) -> WeComMessage:
        headers = payload.get("headers")
        body = payload.get("body")
        if not isinstance(headers, dict) or not isinstance(body, dict):
            raise WeComMalformedFrameError("message callback headers/body must be objects")
        request_id = self._require_non_empty_string(headers.get("req_id"), "message req_id")
        message_id = self._require_non_empty_string(body.get("msgid"), "message msgid")
        message_type = self._require_non_empty_string(body.get("msgtype"), "message msgtype")
        callback_bot_id = body.get("aibotid")
        if callback_bot_id is None:
            raise WeComMalformedFrameError("message callback is missing aibotid")
        callback_bot_id = self._require_non_empty_string(callback_bot_id, "message aibotid")
        if callback_bot_id != self._bot_id:
            raise WeComMalformedFrameError("message callback bot id mismatch")

        sender = body.get("from") or {}
        if not isinstance(sender, dict):
            raise WeComMalformedFrameError("message callback sender must be an object")
        user_id = sender.get("userid")
        if user_id is not None:
            user_id = self._require_non_empty_string(user_id, "message userid")
        chat_id = body.get("chatid")
        if chat_id is not None:
            chat_id = self._require_non_empty_string(chat_id, "message chatid")
        chat_type = body.get("chattype")
        if chat_type is not None:
            chat_type = self._require_non_empty_string(chat_type, "message chattype")

        text = ""
        if message_type == "text":
            text_body = body.get("text")
            if not isinstance(text_body, dict):
                raise WeComMalformedFrameError("message text must be an object")
            text = self._require_non_empty_string(text_body.get("content"), "message text content")

        return WeComMessage(
            message_id=message_id,
            request_id=request_id,
            bot_id=callback_bot_id,
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            message_type=message_type,
            text=text,
            raw_body=body,
        )

    def _fail_pending(self, error: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    @staticmethod
    def _ensure_success(response: dict[str, Any], operation: str, *, req_id: str) -> None:
        headers = response.get("headers")
        if not isinstance(headers, dict):
            raise WeComResponseError(f"{operation} response headers must be an object")
        response_req_id = headers.get("req_id")
        if not isinstance(response_req_id, str) or response_req_id != req_id:
            raise WeComResponseError(f"{operation} response request id mismatch")
        command = WeComWebSocketProtocol._normalize_command(response.get("cmd"))
        expected_command = {
            "subscribe": "aibot_subscribe",
            "respond": "aibot_respond_msg",
            "ping": "ping",
        }[operation]
        if command is not None and command != expected_command:
            raise WeComResponseError(f"{operation} response command mismatch")
        errcode = response.get("errcode")
        if isinstance(errcode, bool) or not isinstance(errcode, int):
            raise WeComResponseError(f"{operation} response errcode is invalid")
        if errcode != 0:
            errmsg = response.get("errmsg")
            detail = errmsg.strip()[:200] if isinstance(errmsg, str) and errmsg.strip() else "unknown error"
            if operation == "subscribe" and errcode in {40001, 40004}:
                raise WeComAuthenticationError(f"{operation} failed with errcode={errcode}: {detail}")
            raise WeComResponseError(f"{operation} failed with errcode={errcode}: {detail}")

    @staticmethod
    def _normalize_command(command: Any) -> str | None:
        if command is None or command == "":
            return None
        if not isinstance(command, str):
            raise WeComMalformedFrameError("websocket command must be a string")
        return command

    @staticmethod
    def _require_non_empty_string(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise WeComProtocolError(f"{field} must be a non-empty string")
        if field == "message text content" and len(value) > 10000:
            raise WeComMalformedFrameError("message text content is too long")
        return value
