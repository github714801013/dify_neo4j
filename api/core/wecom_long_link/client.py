from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable

from extensions.ext_redis import redis_client

from .config import WeComBotConfig
from .protocol import WeComMessage, WeComWebSocketProtocol
from .state import lease_key, message_key
from .store import RedisStateStore

WE_COM_WS_URL = "wss://openws.work.weixin.qq.com"
logger = logging.getLogger(__name__)


async def connect_websocket(url: str = WE_COM_WS_URL):
    import websockets

    return await websockets.connect(
        url,
        ping_interval=None,
        open_timeout=15,
        close_timeout=10,
        max_size=1024 * 1024,
    )


class WeComOutboundClient:
    def __init__(self, config: WeComBotConfig, *, socket_factory=connect_websocket, state_store=None) -> None:
        self.config = config
        self._socket_factory = socket_factory
        self._state_store = state_store or RedisStateStore(redis_client)
        self._socket = None
        self._protocol = None
        self._lease_task: asyncio.Task[None] | None = None

    async def _renew_lease(self, key: str, owner: str, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except TimeoutError:
                pass
            if stop_event.is_set():
                return
            if not self._state_store.renew(key, owner, 30):
                raise RuntimeError("WeCom lease renewal failed")

    async def _ping_loop(self, protocol: WeComWebSocketProtocol, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=25)
            except TimeoutError:
                pass
            if stop_event.is_set():
                return
            await asyncio.wait_for(protocol.ping(req_id=str(uuid.uuid4())), timeout=15)

    async def _run_message_handler(
        self,
        message: WeComMessage,
        stream_id: str,
        on_message: Callable[..., Awaitable[str | None]] | None,
        ping_task: asyncio.Task[None],
    ) -> str | None:
        if not on_message or not self._protocol:
            return None
        handler_task = asyncio.create_task(on_message(message, self._protocol, stream_id))
        connection_task = asyncio.create_task(self._protocol.wait_closed())
        try:
            done, _ = await asyncio.wait(
                {handler_task, self._lease_task, ping_task, connection_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if connection_task in done:
                connection_task.result()
            if self._lease_task in done:
                self._lease_task.result()
            if ping_task in done:
                ping_task.result()
            if handler_task not in done:
                handler_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await handler_task
                raise asyncio.CancelledError
            return handler_task.result()
        finally:
            for task in (handler_task, connection_task):
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def run(self, *, on_message: Callable[..., Awaitable[str | None]] | None = None) -> None:
        while self.config.enabled:
            owner = str(uuid.uuid4())
            key = lease_key(
                tenant_id=self.config.tenant_id,
                instance_id=self.config.instance_id,
                bot_id=self.config.bot_id,
            )
            if not self._state_store.acquire(key, owner, 30):
                await asyncio.sleep(1)
                continue
            try:
                self._socket = await asyncio.wait_for(self._socket_factory(WE_COM_WS_URL), timeout=15)
                self._protocol = WeComWebSocketProtocol(
                    socket=self._socket,
                    bot_id=self.config.bot_id,
                    secret=self.config.secret,
                )
                await asyncio.wait_for(self._protocol.subscribe(req_id=str(uuid.uuid4())), timeout=15)
                logger.info(
                    "WeCom long-link subscribed tenant=%s instance=%s bot=%s",
                    self.config.tenant_id,
                    self.config.instance_id,
                    self.config.bot_id,
                )
                lease_stop = asyncio.Event()
                self._lease_task = asyncio.create_task(self._renew_lease(key, owner, lease_stop))
                ping_task = asyncio.create_task(self._ping_loop(self._protocol, lease_stop))
                while True:
                    message_task = asyncio.create_task(self._protocol.receive_message())
                    connection_task = asyncio.create_task(self._protocol.wait_closed())
                    try:
                        done, _ = await asyncio.wait(
                            {message_task, self._lease_task, ping_task, connection_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if connection_task in done:
                            connection_task.result()
                        if self._lease_task in done:
                            self._lease_task.result()
                        if ping_task in done:
                            ping_task.result()
                        if message_task not in done:
                            continue
                        message = await message_task
                    finally:
                        for task in (message_task, connection_task):
                            if not task.done():
                                task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                    logger.info(
                        "WeCom long-link message received tenant=%s instance=%s bot=%s msgid=%s type=%s",
                        self.config.tenant_id,
                        self.config.instance_id,
                        self.config.bot_id,
                        message.message_id,
                        message.message_type,
                    )
                    message_key_value = message_key(
                        tenant_id=self.config.tenant_id,
                        instance_id=self.config.instance_id,
                        bot_id=self.config.bot_id,
                        message_id=message.message_id,
                    )
                    state = self._state_store.get(message_key_value)
                    if state and state[0].value == "DIFY_COMPLETED" and state[1]:
                        await asyncio.wait_for(self._protocol.respond_text(message, state[1]), timeout=15)
                        self._state_store.mark_reply_sent(message_key_value)
                        continue
                    if state and state[0].value == "REPLY_SENT":
                        continue
                    if not self._state_store.claim(message_key_value):
                        continue
                    try:
                        stream_id = str(uuid.uuid4())
                        content = await self._run_message_handler(message, stream_id, on_message, ping_task)
                    except BaseException:
                        self._state_store.reset_processing(message_key_value)
                        raise
                    if content:
                        if len(content) > 10000:
                            raise ValueError("WeCom response content is too long")
                        self._state_store.mark_completed(message_key_value, content)
                        self._state_store.mark_reply_sent(message_key_value)
                    else:
                        self._state_store.reset_processing(message_key_value)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "WeCom long-link iteration failed tenant=%s instance=%s bot=%s",
                    self.config.tenant_id,
                    self.config.instance_id,
                    self.config.bot_id,
                    exc_info=True,
                )
                await asyncio.sleep(1)
            finally:
                if "lease_stop" in locals():
                    lease_stop.set()
                if "ping_task" in locals():
                    ping_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await ping_task
                if self._lease_task:
                    self._lease_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._lease_task
                    self._lease_task = None
                self._state_store.release(key, owner)
                if self._protocol:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(self._protocol.close(), timeout=10)
                self._socket = None
                self._protocol = None


class WeComLongLinkManager:
    def __init__(self, provider, *, client_factory=WeComOutboundClient, state_store=None) -> None:
        self._provider = provider
        self._client_factory = client_factory
        self._state_store = state_store or RedisStateStore(redis_client)
        self._tasks: list[asyncio.Task] = []

    async def start(self, *, on_message: Callable[[WeComMessage], Awaitable[str | None]] | None = None) -> None:
        for config in self._provider():
            if isinstance(config, dict):
                config = WeComBotConfig(**config)
            if config.enabled:
                client = self._client_factory(config, state_store=self._state_store)
                self._tasks.append(asyncio.create_task(client.run(on_message=on_message)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
