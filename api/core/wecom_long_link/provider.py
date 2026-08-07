from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from services.plugin.endpoint_service import EndpointService

from .config import WeComBotConfig

WE_COM_BOT_PLUGIN_ID = "langgenius/wecom-bot"


def _endpoint_value(endpoint: Any, key: str, default: Any = None) -> Any:
    if isinstance(endpoint, dict):
        return endpoint.get(key, default)
    return getattr(endpoint, key, default)


def _settings(endpoint: Any) -> dict[str, Any]:
    value = _endpoint_value(endpoint, "settings", {})
    return value if isinstance(value, dict) else {}


def _value(settings: dict[str, Any], key: str) -> str | None:
    value = settings.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _app_id(settings: dict[str, Any]) -> str | None:
    app = settings.get("app")
    if isinstance(app, str):
        return app.strip() or None
    if isinstance(app, dict):
        for key in ("id", "app_id", "value"):
            value = app.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _is_masked(value: str) -> bool:
    stripped = value.strip()
    return len(stripped) >= 3 and len(set(stripped)) == 1 and stripped[0] in "*xX•"


def _instance_id(endpoint: Any) -> str | None:
    for key in ("hook_id", "id", "name"):
        value = _endpoint_value(endpoint, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _config_version(endpoint: Any, *, bot_id: str, secret: str, app_id: str, enabled: bool) -> str:
    explicit = _endpoint_value(endpoint, "config_version")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    updated_at = _endpoint_value(endpoint, "updated_at")
    if updated_at is not None:
        return str(updated_at)
    material = "\x1f".join((bot_id, secret, app_id, str(enabled)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def load_wecom_bot_configs(
    tenant_id: str,
    user_id: str,
    *,
    endpoint_service: Any = EndpointService,
    page_size: int = 100,
) -> list[WeComBotConfig]:
    """读取企业微信 endpoint 配置；掩码 secret 会被明确拒绝。"""
    endpoints = []
    page = 1
    while True:
        batch = endpoint_service.list_endpoints_for_single_plugin(
            tenant_id, user_id, WE_COM_BOT_PLUGIN_ID, page, page_size
        ) or []
        endpoints.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    result: list[WeComBotConfig] = []
    for endpoint in endpoints or []:
        settings = _settings(endpoint)
        bot_id = _value(settings, "bot_id")
        secret = _value(settings, "secret")
        app_id = _app_id(settings)
        instance_id = _instance_id(endpoint)
        if not bot_id or not secret or not app_id or not instance_id:
            continue
        if _is_masked(secret):
            raise ValueError("masked WeCom secret cannot be used by worker")
        enabled = _endpoint_value(endpoint, "enabled", True)
        if not isinstance(enabled, bool):
            raise TypeError("WeCom endpoint enabled must be a bool")
        result.append(
            WeComBotConfig(
                tenant_id=tenant_id,
                instance_id=instance_id,
                bot_id=bot_id,
                secret=secret,
                app_id=app_id,
                enabled=enabled,
                config_version=_config_version(
                    endpoint, bot_id=bot_id, secret=secret, app_id=app_id, enabled=enabled
                ),
            )
        )
    return result


def build_config_provider(
    tenant_id: str, user_id: str, *, endpoint_service: Any = EndpointService
) -> Callable[[], Sequence[WeComBotConfig]]:
    return lambda: load_wecom_bot_configs(tenant_id, user_id, endpoint_service=endpoint_service)
