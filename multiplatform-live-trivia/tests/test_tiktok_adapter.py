"""TikTok adapter tests."""
import asyncio

import pytest

import app.integrations.tiktok as tiktok_module
from app.integrations.tiktok import TikTokAdapter
from app.models import PlatformStatusType


class FakeTikTokClient:
    """Fake TikTok client that fails once, then connects."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.calls = 0
        self.is_connected = False

    async def start(self):
        self.calls += 1

        if self.calls == 1:
            raise RuntimeError("temporary TikTok connection failure")

        await self.adapter._on_connect(None)
        self.is_connected = True
        self.adapter._is_running = False

    async def disconnect(self):
        self.is_connected = False

    def on(self, event):
        def decorator(callback):
            return callback

        return decorator


@pytest.mark.asyncio
async def test_tiktok_recovers_from_temporary_connection_error(monkeypatch):
    queue = asyncio.Queue(maxsize=10)

    adapter = TikTokAdapter(
        username="test_creator",
        chat_queue=queue,
    )

    fake_client = FakeTikTokClient(adapter)
    adapter.client = fake_client

    async def fake_sleep(_delay):
        pass

    monkeypatch.setattr(
        tiktok_module.asyncio,
        "sleep",
        fake_sleep,
    )

    await adapter.start()

    assert fake_client.calls == 2
    assert adapter._is_running is False
    assert adapter.status.state == PlatformStatusType.CONNECTED
    assert adapter.status.detail == (
        "TikTok LIVE connected as @test_creator"
    )