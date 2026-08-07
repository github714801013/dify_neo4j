from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from .config import WeComBotConfig


@dataclass
class _RunningClient:
    version: str
    client: object
    task: asyncio.Task[None]


class WeComConfigReconciler:
    """根据 endpoint 配置版本增删和替换长连接客户端。"""

    def __init__(self, provider: Callable[[], list[WeComBotConfig]], client_factory: Callable) -> None:
        self._provider = provider
        self._client_factory = client_factory
        self._running: dict[str, _RunningClient] = {}

    async def reconcile(self, on_message=None) -> None:
        configs = {config.instance_id: config for config in self._provider() if config.enabled}
        for instance_id, running in list(self._running.items()):
            if running.task.done():
                await asyncio.gather(running.task, return_exceptions=True)
                self._running.pop(instance_id, None)
                continue
            config = configs.get(instance_id)
            if config is None or config.config_version != running.version:
                running.task.cancel()
                await asyncio.gather(running.task, return_exceptions=True)
                self._running.pop(instance_id, None)
        for instance_id, config in configs.items():
            if instance_id in self._running:
                continue
            client = self._client_factory(config)
            task = asyncio.create_task(client.run(on_message=on_message))
            self._running[instance_id] = _RunningClient(config.config_version, client, task)

    async def stop(self) -> None:
        tasks = [running.task for running in self._running.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._running.clear()
