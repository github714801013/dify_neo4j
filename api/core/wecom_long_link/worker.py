from __future__ import annotations

import asyncio

from sqlalchemy import select

from models.account import Tenant
from models.engine import db

from .app_router import WeComAppRouter
from .client import WeComOutboundClient
from .provider import build_config_provider
from .reconciler import WeComConfigReconciler

SYSTEM_USER_ID = "system"


def _tenant_ids() -> list[str]:
    with db.engine.connect() as connection:
        return list(connection.scalars(select(Tenant.id)).all())


async def _run_tenant(tenant_id: str) -> None:
    provider = build_config_provider(tenant_id, SYSTEM_USER_ID)

    async def on_message(message):
        configs = provider()
        config = next((item for item in configs if item.bot_id == message.bot_id and item.enabled), None)
        if config is None or not message.user_id:
            return None
        return await asyncio.to_thread(
            WeComAppRouter().generate_reply,
            tenant_id=tenant_id,
            app_id=config.app_id,
            user_id=message.user_id,
            query=message.text,
            conversation_id=message.chat_id,
        )

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
    await asyncio.gather(*(_run_tenant(tenant_id) for tenant_id in _tenant_ids()))


if __name__ == "__main__":
    asyncio.run(run())
