from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any

from core.db.session_factory import create_session
from core.plugin.backwards_invocation.app import PluginAppBackwardsInvocation
from models.model import App, AppMode

logger = logging.getLogger(__name__)


APP_STREAM_ERROR_CODE = "APP_STREAM_ERROR"
APP_STREAM_ERROR_MESSAGE = f"服务暂时不可用，请稍后重试。错误码：{APP_STREAM_ERROR_CODE}"


class WeComAppStreamError(RuntimeError):
    """Dify app stream failure with an internal detail for logs."""

    def __init__(self, detail: str, *, code: str | None = None, status: int | None = None) -> None:
        self.detail = detail or "Dify app stream returned an error"
        self.code = code
        self.status = status
        super().__init__(self.detail)


@dataclass(frozen=True)
class WeComAppEvent:
    kind: str
    content: str | None = None
    workflow_run_id: str | None = None
    node_execution_id: str | None = None
    node_id: str | None = None
    node_type: str | None = None
    node_title: str | None = None
    elapsed_time: float | None = None


class WeComAppRouter:
    """将企业微信消息路由到已配置的 Dify Chat App。"""

    def generate_reply(
        self, *, tenant_id: str, app_id: str, user_id: str, query: str, conversation_id: str | None = None
    ) -> str:
        answer = ""
        for event in self.stream_reply(
            tenant_id=tenant_id,
            app_id=app_id,
            user_id=user_id,
            query=query,
            conversation_id=conversation_id,
        ):
            if event.kind == "final" and event.content:
                answer = event.content
        if not answer:
            raise ValueError("Dify app returned no text answer")
        return answer

    def stream_reply(
        self, *, tenant_id: str, app_id: str, user_id: str, query: str, conversation_id: str | None = None
    ) -> Generator[WeComAppEvent, None, None]:
        correlation_id = str(uuid.uuid4())
        started_at = time.monotonic()
        logger.info(
            "WeCom app invocation started correlation_id=%s tenant=%s app=%s user=%s",
            correlation_id,
            tenant_id,
            app_id,
            user_id,
        )
        try:
            with create_session() as session:
                app = session.get(App, app_id)
                if app is None or app.tenant_id != tenant_id:
                    raise ValueError("configured Dify app does not exist")
                if app.mode not in {AppMode.CHAT, AppMode.AGENT_CHAT, AppMode.ADVANCED_CHAT, AppMode.WORKFLOW}:
                    raise ValueError("configured Dify app is not supported")
                response = PluginAppBackwardsInvocation.invoke_app(
                    session=session,
                    app_id=app_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    query=query,
                    stream=True,
                    inputs={},
                    files=[],
                )
                if not isinstance(response, Generator):
                    answer = self._extract_answer(response)
                    yield WeComAppEvent(kind="final", content=answer)
                    return

                answer_parts: list[str] = []
                seen_nodes: set[tuple[str | None, str | None]] = set()
                for response_event in response:
                    event = self._unwrap_event(response_event)
                    if event is None:
                        continue
                    event_name = self._event_name(event)
                    if event_name == "node_finished":
                        summary = self._node_summary(event, seen_nodes)
                        if summary:
                            yield summary
                    elif event_name in {"message", "agent_message", "text_chunk"}:
                        text = self._event_text(event)
                        if text:
                            answer_parts.append(text)
                            yield WeComAppEvent(kind="answer_chunk", content=text)
                    elif event_name == "text_replace":
                        text = self._event_text(event)
                        if text:
                            answer_parts = [text]
                            yield WeComAppEvent(kind="answer_replace", content=text)
                    elif event_name == "error":
                        message, code, status = self._error_details(event)
                        raise WeComAppStreamError(message, code=code, status=status)

                answer = "".join(answer_parts)
                if not answer:
                    raise ValueError("Dify app returned no text answer")
                yield WeComAppEvent(kind="final", content=answer)
        except WeComAppStreamError as exc:
            logger.warning(
                "WeCom app invocation failed correlation_id=%s tenant=%s app=%s user=%s "
                "duration_ms=%s error_type=%s error_code=%s error_status=%s error_detail=%s",
                correlation_id,
                tenant_id,
                app_id,
                user_id,
                int((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
                exc.code,
                exc.status,
                exc.detail,
                exc_info=True,
            )
            raise
        except Exception as exc:
            logger.warning(
                "WeCom app invocation failed correlation_id=%s tenant=%s app=%s user=%s "
                "duration_ms=%s error_type=%s error_detail=%s",
                correlation_id,
                tenant_id,
                app_id,
                user_id,
                int((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
                str(exc),
                exc_info=True,
            )
            if isinstance(exc, ValueError):
                raise
            raise WeComAppStreamError(str(exc) or type(exc).__name__) from exc
        else:
            logger.info(
                "WeCom app invocation completed correlation_id=%s tenant=%s app=%s user=%s duration_ms=%s",
                correlation_id,
                tenant_id,
                app_id,
                user_id,
                int((time.monotonic() - started_at) * 1000),
            )

    @staticmethod
    def _error_details(event: Any) -> tuple[str, str | None, int | None]:
        if isinstance(event, Mapping):
            message = event.get("message") or event.get("error")
            code = event.get("code")
            status = event.get("status")
        else:
            message = getattr(event, "message", None) or getattr(event, "error", None)
            code = getattr(event, "code", None)
            status = getattr(event, "status", None)
        return (
            str(message or "Dify app stream returned an error"),
            code if isinstance(code, str) else None,
            status if isinstance(status, int) else None,
        )

    @staticmethod
    def _unwrap_event(response_event: Any) -> Any | None:
        return getattr(response_event, "stream_response", response_event)

    @staticmethod
    def _event_name(event: Any) -> str | None:
        value = getattr(event, "event", None)
        if value is None and isinstance(event, Mapping):
            value = event.get("event")
        return getattr(value, "value", value) if isinstance(getattr(value, "value", value), str) else None

    @staticmethod
    def _event_text(event: Any) -> str | None:
        if isinstance(event, Mapping):
            answer = event.get("answer")
            data = event.get("data")
        else:
            answer = getattr(event, "answer", None)
            data = getattr(event, "data", None)
        if isinstance(answer, str):
            return answer
        if isinstance(data, Mapping):
            text = data.get("text")
        else:
            text = getattr(data, "text", None)
        return text if isinstance(text, str) else None

    @classmethod
    def _node_summary(
        cls, event: Any, seen_nodes: set[tuple[str | None, str | None]]
    ) -> WeComAppEvent | None:
        data = event.get("data") if isinstance(event, Mapping) else getattr(event, "data", None)
        if data is None:
            return None
        get = data.get if isinstance(data, Mapping) else lambda key, default=None: getattr(data, key, default)
        execution_id = get("id")
        node_id = get("node_id")
        if not isinstance(execution_id, str) or not isinstance(node_id, str):
            return None
        key = (execution_id, node_id)
        if key in seen_nodes:
            return None
        seen_nodes.add(key)
        title = get("title")
        node_type = get("node_type")
        elapsed_time = get("elapsed_time")
        title = title if isinstance(title, str) else "未命名节点"
        node_type = node_type if isinstance(node_type, str) else "unknown"
        elapsed_time = elapsed_time if isinstance(elapsed_time, int | float) else None
        content = f"{title}…"
        workflow_run_id = (
            event.get("workflow_run_id")
            if isinstance(event, Mapping)
            else getattr(event, "workflow_run_id", None)
        )
        return WeComAppEvent(
            kind="node_finished",
            content=content,
            workflow_run_id=workflow_run_id if isinstance(workflow_run_id, str) else None,
            node_execution_id=execution_id,
            node_id=node_id,
            node_type=node_type,
            node_title=title,
            elapsed_time=elapsed_time,
        )

    @staticmethod
    def _extract_answer(response: Any) -> str:
        if isinstance(response, Mapping):
            answer = response.get("answer")
            if isinstance(answer, str):
                return answer
            data = response.get("data")
            if isinstance(data, Mapping) and isinstance(data.get("answer"), str):
                return data["answer"]
        answer = getattr(response, "answer", None)
        if isinstance(answer, str):
            return answer
        data = getattr(response, "data", None)
        answer = getattr(data, "answer", None)
        if isinstance(answer, str):
            return answer
        raise ValueError("Dify app returned no text answer")
