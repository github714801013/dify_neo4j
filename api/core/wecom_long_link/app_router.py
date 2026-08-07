from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.db.session_factory import create_session
from core.plugin.backwards_invocation.app import PluginAppBackwardsInvocation
from models.model import App, AppMode


class WeComAppRouter:
    """将企业微信消息路由到已配置的 Dify Chat App。"""

    def __init__(self) -> None:
        pass

    def generate_reply(
        self, *, tenant_id: str, app_id: str, user_id: str, query: str, conversation_id: str | None = None
    ) -> str:
        with create_session() as session:
            app = session.get(App, app_id)
            if app is None or app.tenant_id != tenant_id:
                raise ValueError("configured Dify app does not exist")
            if app.mode not in {AppMode.CHAT, AppMode.AGENT_CHAT, AppMode.ADVANCED_CHAT}:
                raise ValueError("configured Dify app is not a chat app")
            response = PluginAppBackwardsInvocation.invoke_app(
                session=session,
                app_id=app_id,
                user_id=user_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                query=query,
                stream=False,
                inputs={},
                files=[],
            )
            return self._extract_answer(response)

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
