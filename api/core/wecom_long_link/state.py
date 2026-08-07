from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class LeaseStore(Protocol):
    def acquire(self, key: str, owner: str, ttl_seconds: int) -> bool: ...

    def renew(self, key: str, owner: str, ttl_seconds: int) -> bool: ...

    def release(self, key: str, owner: str) -> bool: ...


class MessageState(StrEnum):
    PROCESSING = "PROCESSING"
    DIFY_COMPLETED = "DIFY_COMPLETED"
    REPLY_SENT = "REPLY_SENT"


class MessageIdempotencyStore(Protocol):
    def claim(self, key: str) -> bool: ...

    def mark_completed(self, key: str, content: str) -> None: ...

    def mark_reply_sent(self, key: str) -> None: ...

    def reset_processing(self, key: str) -> None: ...

    def get(self, key: str) -> tuple[MessageState, str | None] | None: ...


def _component(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("state key components must be strings")
    return f"{len(value)}:{value}"


def lease_key(*, tenant_id: str, instance_id: str, bot_id: str) -> str:
    return ":".join(
        (
            "wecom",
            "longlink",
            "lease",
            _component(tenant_id),
            _component(instance_id),
            _component(bot_id),
        )
    )


def message_key(*, tenant_id: str, instance_id: str, bot_id: str, message_id: str) -> str:
    return ":".join(
        (
            "wecom",
            "longlink",
            "message",
            _component(tenant_id),
            _component(instance_id),
            _component(bot_id),
            _component(message_id),
        )
    )
