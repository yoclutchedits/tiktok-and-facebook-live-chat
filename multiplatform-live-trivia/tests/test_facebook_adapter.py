
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
@pytest.mark.asyncio
async def test_facebook_recovers_from_temporary_polling_error(monkeypatch):
    queue = asyncio.Queue(maxsize=10)

    adapter = FacebookAdapter(
        access_token="test-token",
        live_video_id="test-live-id",
        chat_queue=queue,
        poll_interval_ms=0,
    )

    class TemporaryFailureClient:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params):
            self.calls += 1

            if self.calls == 1:
                raise RuntimeError("temporary Facebook API failure")

            self.adapter._running = False

            return FakeResponse()

    fake_client = TemporaryFailureClient()
    fake_client.adapter = adapter

    monkeypatch.setattr(
        facebook_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake_client,
    )

    adapter._running = True

    await adapter._poll_loop()

    assert fake_client.calls == 2
    assert adapter.status.reconnect_attempts == 1
    assert adapter.status.state.value == "CONNECTED"
    
import time
from types import SimpleNamespace

from app.core.engine import TriviaEngine
from app.integrations.tiktok import TikTokAdapter
from app.models import (
    GameCommand,
    GameCommandType,
    Question,
)


@pytest.mark.asyncio
async def test_facebook_failure_does_not_stop_tiktok_gameplay(
    monkeypatch,
):
    queue = asyncio.Queue(maxsize=100)

    engine = TriviaEngine(
        questions=[
            Question(
                question="Capital of France?",
                answers=("Paris",),
            )
        ],
        chat_queue=queue,
        question_duration_sec=1.0,
        result_duration_sec=0.01,
        transition_duration_sec=0.01,
    )

    tiktok = TikTokAdapter(
        username="test_creator",
        chat_queue=queue,
    )

    facebook = FacebookAdapter(
        access_token="test-token",
        live_video_id="test-live-id",
        chat_queue=queue,
        poll_interval_ms=0,
    )

    class FailingFacebookClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params):
            facebook._running = False
            raise RuntimeError("Facebook temporarily disconnected")

    monkeypatch.setattr(
        facebook_module.httpx,
        "AsyncClient",
        lambda **kwargs: FailingFacebookClient(),
    )

    engine.start()

    engine.handle_command(
        GameCommand(
            command=GameCommandType.START
        )
    )

    await asyncio.sleep(0.05)

    assert engine.state.value == "ACTIVE"

    facebook._running = True

    await facebook._poll_loop()

    assert facebook.status.reconnect_attempts == 1

    user = SimpleNamespace(
        user_id="tiktok-user-1",
        nickname="Alice",
        unique_id="alice",
    )

    comment_event = SimpleNamespace(
        user=user,
        comment="Paris",
        comment_id="comment-1",
    )

    await tiktok._on_comment(comment_event)

    for _ in range(20):
        leaderboard = engine.get_snapshot().leaderboard

        if leaderboard:
            break

        await asyncio.sleep(0.01)

    assert len(leaderboard) == 1
    assert leaderboard[0]["display_name"] == "Alice"
    assert leaderboard[0]["score"] == 1
    assert engine._running is True

    await engine.stop()