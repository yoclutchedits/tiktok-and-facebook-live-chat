
import asyncio

import pytest

import app.integrations.facebook as facebook_module
from app.integrations.facebook import FacebookAdapter
from app.models import ChatMessage


class QueueFullOnce(asyncio.Queue):
    """Simulate a full queue once, then allow the retry."""

    def __init__(self):
        super().__init__()
        self.failed_once = False

    def put_nowait(self, item):
        if not self.failed_once:
            self.failed_once = True
            raise asyncio.QueueFull

        return super().put_nowait(item)


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "data": [
                {
                    "id": "comment-123",
                    "from": {
                        "id": "user-1",
                        "name": "Test User",
                    },
                    "message": "London",
                    "created_time": "2026-10-03T10:00:00Z",
                }
            ]
        }


class FakeAsyncClient:
    def __init__(self, adapter):
        self.adapter = adapter
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params):
        self.calls += 1

        # Stop after returning the second response. The second response
        # should retry the comment that failed to enqueue on the first.
        if self.calls == 2:
            self.adapter._running = False

        return FakeResponse()


@pytest.mark.asyncio
async def test_facebook_retries_comment_when_queue_is_full(monkeypatch):
    queue = QueueFullOnce()

    adapter = FacebookAdapter(
        access_token="test-token",
        live_video_id="test-live-id",
        chat_queue=queue,
        poll_interval_ms=0,
    )

    fake_client = FakeAsyncClient(adapter)

    monkeypatch.setattr(
        facebook_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake_client,
    )

    adapter._running = True
    await adapter._poll_loop()

    # The first enqueue failed, but the next poll successfully retried it.
    assert fake_client.calls == 2
    assert adapter.dropped_messages == 1
    assert "comment-123" in adapter.seen_message_ids

    message = queue.get_nowait()
    assert message.message_id == "comment-123"
    assert message.text == "London"
    assert queue.empty()
