from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

from .state import LeaseStore, MessageIdempotencyStore, MessageState


@dataclass
class _Lease:
    owner: str
    expires_at: float


class InMemoryStateStore(LeaseStore, MessageIdempotencyStore):
    """可测试的进程内实现。"""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._leases: dict[str, _Lease] = {}
        self._messages: dict[str, tuple[MessageState, str | None]] = {}
        self._lock = Lock()

    def acquire(self, key: str, owner: str, ttl_seconds: int) -> bool:
        with self._lock:
            current = self._leases.get(key)
            now = self._clock()
            if current and current.expires_at > now and current.owner != owner:
                return False
            self._leases[key] = _Lease(owner, now + ttl_seconds)
            return True

    def renew(self, key: str, owner: str, ttl_seconds: int) -> bool:
        with self._lock:
            current = self._leases.get(key)
            now = self._clock()
            if not current or current.owner != owner or current.expires_at <= now:
                return False
            self._leases[key] = _Lease(owner, now + ttl_seconds)
            return True

    def release(self, key: str, owner: str) -> bool:
        with self._lock:
            current = self._leases.get(key)
            if not current or current.owner != owner:
                return False
            del self._leases[key]
            return True

    def claim(self, key: str) -> bool:
        with self._lock:
            if key in self._messages:
                return False
            self._messages[key] = (MessageState.PROCESSING, None)
            return True

    def mark_completed(self, key: str, content: str) -> None:
        with self._lock:
            self._messages[key] = (MessageState.DIFY_COMPLETED, content)

    def mark_reply_sent(self, key: str) -> None:
        with self._lock:
            content = self._messages.get(key, (MessageState.REPLY_SENT, None))[1]
            self._messages[key] = (MessageState.REPLY_SENT, content)

    def reset_processing(self, key: str) -> None:
        with self._lock:
            if self._messages.get(key, (None, None))[0] == MessageState.PROCESSING:
                del self._messages[key]

    def get(self, key: str) -> tuple[MessageState, str | None] | None:
        with self._lock:
            return self._messages.get(key)


class RedisStateStore(LeaseStore, MessageIdempotencyStore):
    """基于 redis-py 的实现，Lua 保证状态和租约检查的原子性。"""

    def __init__(self, client) -> None:
        self._client = client

    def acquire(self, key: str, owner: str, ttl_seconds: int) -> bool:
        return bool(self._client.set(key, owner, nx=True, ex=ttl_seconds))

    def renew(self, key: str, owner: str, ttl_seconds: int) -> bool:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('expire', KEYS[1], ARGV[2]) "
            "else return 0 end"
        )
        return bool(self._client.eval(script, 1, key, owner, ttl_seconds))

    def release(self, key: str, owner: str) -> bool:
        script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
        return bool(self._client.eval(script, 1, key, owner))

    def claim(self, key: str) -> bool:
        return bool(
            self._client.eval(
                "if redis.call('exists', KEYS[1]) == 1 then return 0 "
                "else redis.call('hset', KEYS[1], 'state', ARGV[1]); "
                "return redis.call('expire', KEYS[1], ARGV[2]) end",
                1,
                key,
                MessageState.PROCESSING.value,
                900,
            )
        )

    def mark_completed(self, key: str, content: str) -> None:
        self._client.hset(key, mapping={"state": MessageState.DIFY_COMPLETED.value, "content": content})
        self._client.expire(key, 86400)

    def mark_reply_sent(self, key: str) -> None:
        self._client.hset(key, "state", MessageState.REPLY_SENT.value)
        self._client.expire(key, 86400)

    def reset_processing(self, key: str) -> None:
        self._client.eval(
            "if redis.call('hget', KEYS[1], 'state') == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            MessageState.PROCESSING.value,
        )

    def get(self, key: str) -> tuple[MessageState, str | None] | None:
        values = self._client.hgetall(key)
        if not values:
            return None
        state = values.get(b"state", values.get("state"))
        content = values.get(b"content", values.get("content"))
        if isinstance(state, bytes):
            state = state.decode()
        if isinstance(content, bytes):
            content = content.decode()
        return MessageState(state), content
