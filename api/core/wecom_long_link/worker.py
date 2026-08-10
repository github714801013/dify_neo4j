from __future__ import annotations

import asyncio
import logging
import threading

from sqlalchemy import select

from app_factory import create_app
from models.account import Tenant
from models.engine import db

from .app_router import WeComAppRouter
from .client import WeComOutboundClient
from .provider import build_config_provider
from .reconciler import WeComConfigReconciler

SYSTEM_USER_ID = "system"
logger = logging.getLogger(__name__)


def _tenant_ids() -> list[str]:
    with db.engine.connect() as connection:
        return list(connection.scalars(select(Tenant.id)).all())


async def _run_tenant(tenant_id: str) -> None:
    provider = build_config_provider(tenant_id, SYSTEM_USER_ID)

    async def on_message(message, protocol, stream_id):
        logger.info(
            "WeCom long-link dispatching message tenant=%s bot=%s msgid=%s user=%s",
            tenant_id,
            message.bot_id,
            message.message_id,
            message.user_id,
        )
        configs = provider()
        config = next((item for item in configs if item.bot_id == message.bot_id and item.enabled), None)
        if config is None or not message.user_id:
            logger.warning(
                "WeCom long-link message ignored tenant=%s bot=%s msgid=%s reason=%s",
                tenant_id,
                message.bot_id,
                message.message_id,
                "missing_config_or_user",
            )
            return None
        stream_task = None
        stop_event = threading.Event()
        try:
            router = WeComAppRouter()
            events: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def stream_events() -> None:
                try:
                    event_stream = router.stream_reply(
                        tenant_id=tenant_id,
                        app_id=config.app_id,
                        user_id=message.user_id,
                        query=message.text,
                        conversation_id=message.chat_id,
                    )
                    for event in event_stream:
                        if stop_event.is_set():
                            event_stream.close()
                            return
                        loop.call_soon_threadsafe(events.put_nowait, event)
                except BaseException as exc:
                    if not stop_event.is_set():
                        loop.call_soon_threadsafe(events.put_nowait, exc)
                finally:
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(events.put_nowait, None)

            stream_task = asyncio.create_task(asyncio.to_thread(stream_events))
            reply = None
            while True:
                event = await events.get()
                if event is None:
                    break
                if isinstance(event, BaseException):
                    raise event
                if event.kind == "node_finished" and event.content:
                    try:
                        await protocol.respond_stream(message, event.content, stream_id=stream_id, finish=False)
                    except Exception:
                        logger.warning(
                            "WeCom long-link node update failed tenant=%s bot=%s msgid=%s node=%s",
                            tenant_id,
                            message.bot_id,
                            message.message_id,
                            event.node_id,
                            exc_info=True,
                        )
                elif event.kind == "final":
                    reply = event.content
            await stream_task
        except Exception as exc:
            logger.warning(
                "WeCom long-link app reply failed tenant=%s bot=%s msgid=%s app=%s error_type=%s",
                tenant_id,
                message.bot_id,
                message.message_id,
                config.app_id,
                type(exc).__name__,
            )
            raise
        finally:
            stop_event.set()
            if stream_task:
                stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
        logger.info(
            "WeCom long-link app reply generated tenant=%s bot=%s msgid=%s app=%s",
            tenant_id,
            message.bot_id,
            message.message_id,
            config.app_id,
        )
        return reply

    reconciler = WeComConfigReconciler(provider, lambda config: WeComOutboundClient(config))
    try:
        while True:
            try:
                await reconciler.reconcile(on_message=on_message)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(30)
                continue
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
    finally:
        await reconciler.stop()


async def run() -> None:
    flask_app = create_app()[1]
    with flask_app.app_context():
        await asyncio.gather(*(_run_tenant(tenant_id) for tenant_id in _tenant_ids()))


if __name__ == "__main__":
    asyncio.run(run())
