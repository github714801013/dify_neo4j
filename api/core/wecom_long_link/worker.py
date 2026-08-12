from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

from sqlalchemy import select

from app_factory import create_app
from models.account import Tenant
from models.engine import db

from .app_router import WeComAppRouter
from .client import WeComOutboundClient
from .feedback import FeedbackCoordinator, FeedbackFrame
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
        feedback_task = None
        timer_task = None
        stop_event = threading.Event()
        cancel_event = asyncio.Event()
        try:
            event_stream_holder: dict[str, object] = {}
            router = WeComAppRouter()
            events: asyncio.Queue = asyncio.Queue()
            frames: asyncio.Queue[FeedbackFrame | None] = asyncio.Queue(maxsize=32)
            loop = asyncio.get_running_loop()

            async def discard_frames() -> None:
                """有界清空待发送帧，避免失败路径永久阻塞 join。"""
                while True:
                    try:
                        frames.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    else:
                        frames.task_done()

            def stop_producer() -> None:
                stop_event.set()
                cancel_event.set()

            def enqueue_frame(frame: FeedbackFrame) -> None:
                if stop_event.is_set() or cancel_event.is_set():
                    return
                try:
                    frames.put_nowait(frame)
                except asyncio.QueueFull:
                    stop_producer()
                    loop.call_soon_threadsafe(events.put_nowait, RuntimeError("feedback frame queue is full"))

            async def send_frames() -> None:
                while True:
                    frame = await frames.get()
                    try:
                        if frame is None:
                            return
                        if cancel_event.is_set() or stop_event.is_set():
                            continue
                        await protocol.respond_stream(
                            message,
                            frame.content,
                            stream_id=frame.stream_id,
                            finish=frame.finish,
                        )
                    except BaseException as exc:
                        if not isinstance(exc, asyncio.CancelledError):
                            stop_producer()
                            coordinator.on_cancelled()
                            await discard_frames()
                            events.put_nowait(exc)
                        raise
                    finally:
                        frames.task_done()

            coordinator = FeedbackCoordinator(
                stream_id=stream_id,
                send=enqueue_frame,
            )
            feedback_task = asyncio.create_task(send_frames())

            async def refresh_feedback() -> None:
                while not cancel_event.is_set():
                    try:
                        await asyncio.wait_for(cancel_event.wait(), timeout=1)
                    except TimeoutError:
                        coordinator.advance()

            timer_task = asyncio.create_task(refresh_feedback())

            def stream_events() -> None:
                try:
                    event_stream = router.stream_reply(
                        tenant_id=tenant_id,
                        app_id=config.app_id,
                        user_id=message.user_id,
                        query=message.text,
                        conversation_id=message.chat_id,
                    )
                    event_stream_holder["stream"] = event_stream
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
                if event.kind == "node_finished":
                    coordinator.on_node_change(event.node_title or event.content)
                elif event.kind == "answer_chunk":
                    coordinator.on_answer_chunk(event.content or "")
                elif event.kind == "answer_replace":
                    coordinator.on_answer_replace(event.content or "")
                elif event.kind == "final":
                    reply = event.content
                    coordinator.on_final_text(event.content or "")
            await stream_task
            await frames.join()
            if feedback_task:
                frames.put_nowait(None)
                await feedback_task
        except asyncio.CancelledError:
            cancel_event.set()
            coordinator.on_cancelled()
            raise
        except Exception as exc:
            cancel_event.set()
            coordinator.on_cancelled()
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
            cancel_event.set()
            stop_event.set()
            event_stream = event_stream_holder.get("stream")
            if event_stream is not None:
                with contextlib.suppress(Exception):
                    event_stream.close()
            await discard_frames()
            for task in (timer_task, stream_task, feedback_task):
                if task:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (timer_task, stream_task, feedback_task) if task),
                return_exceptions=True,
            )
            with contextlib.suppress(asyncio.QueueFull):
                frames.put_nowait(None)
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
