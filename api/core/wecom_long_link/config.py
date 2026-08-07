from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, repr=False)
class WeComBotConfig:
    tenant_id: str
    instance_id: str
    bot_id: str
    secret: str
    app_id: str
    enabled: bool
    config_version: str

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "instance_id", "bot_id", "app_id", "config_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.secret, str) or not self.secret.strip() or self._is_masked(self.secret):
            raise ValueError("a usable WeCom secret is required")
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")

    def __repr__(self) -> str:
        return (
            "WeComBotConfig("
            f"tenant_id={self.tenant_id!r}, instance_id={self.instance_id!r}, "
            f"bot_id={self.bot_id!r}, app_id={self.app_id!r}, "
            f"enabled={self.enabled!r}, config_version={self.config_version!r})"
        )

    @staticmethod
    def _is_masked(value: str) -> bool:
        stripped = value.strip()
        return len(stripped) >= 3 and len(set(stripped)) == 1 and stripped[0] in "*xX•"


class WeComBotConfigProvider(Protocol):
    def get_enabled_bots(self) -> Sequence[WeComBotConfig]: ...
