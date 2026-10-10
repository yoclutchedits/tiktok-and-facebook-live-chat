"""Tests for the current Social Stream Ninja Facebook adapter."""

import asyncio
import json

import pytest

import app.integrations.facebook as facebook_module
from app.integrations.facebook import FacebookAdapter


class FakeWebSocket:
    def __init__(self, messages, adapter):
        self._messages = iter(messages)
        self.adapter = adapter

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def recv(self):
        """Simulate receiving one WebSocket message."""
        try:
            return next(self._messages)
        except StopIteration:
            self.adapter._running = False
            raise RuntimeError("Fake WebSocket stream exhausted")

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.recv()
        except RuntimeError:
            raise StopAsyncIteration

class FakeConnect:
    def __init__(self, websocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb):
        return False


class QueueFullOnce(asyncio.Queue):
    """Fail the first enqueue, then allow later enqueues."""

    def __init__(self):
        super().__init__(maxsize=10)
        self.failed_once = False

    def put_nowait(self, item):
        if not self.failed_once:
            self.failed_once = True
            raise asyncio.QueueFull
        return super().put_nowait(item)


def facebook_payload(
    *,
    message_id="comment-1",
    text="Paris",
    user_id="user-1",
    username="Test User",
):
    return json.dumps(
        {
            "type": "facebook",
            "id": message_id,
            "userid": user_id,
            "chatname": username,
            "chatmessage": text,
            "meta": {
                "plainText": text,
            },
        }
    )


@pytest.mark.asyncio
async def test_facebook_message_reaches_queue(monkeypatch):
    queue = asyncio.Queue(maxsize=10)

    adapter = FacebookAdapter(
        session_id="test-session",
        chat_queue=queue,
    )

    messages = [
        json.dumps(
            {
                "type": "tiktok",
                "userid": "ignored-user",
                "chatname": "Ignored",
                "chatmessage": "ignore me",
            }
        ),
        facebook_payload(
            text="TRIVIA_TEST_123",
            user_id="facebook-user-1",
            username="Facebook User",
        ),
    ]

    websocket = FakeWebSocket(messages, adapter)

    monkeypatch.setattr(
        facebook_module.websockets,
        "connect",
        lambda *args, **kwargs: FakeConnect(websocket),
    )

    adapter._running = True
    await adapter._listen_loop()

    assert queue.qsize() == 1

    message = queue.get_nowait()

    assert message.platform == "facebook"
    assert message.user_id == "facebook-user-1"
    assert message.username == "Facebook User"
    assert message.text == "TRIVIA_TEST_123"


@pytest.mark.asyncio
async def test_duplicate_message_id_is_ignored(monkeypatch):
    queue = asyncio.Queue(maxsize=10)

    adapter = FacebookAdapter(
        session_id="test-session",
        chat_queue=queue,
    )

    payload = facebook_payload(
        message_id="duplicate-123",
        text="Paris",
    )

    websocket = FakeWebSocket(
        [payload, payload],
        adapter,
    )

    monkeypatch.setattr(
        facebook_module.websockets,
        "connect",
        lambda *args, **kwargs: FakeConnect(websocket),
    )

    adapter._running = True
    await adapter._listen_loop()

    assert queue.qsize() == 1
    assert "duplicate-123" in adapter.seen_message_ids

    queue.get_nowait()


@pytest.mark.asyncio
async def test_queue_full_does_not_permanently_mark_message_seen(
    monkeypatch,
):
    queue = QueueFullOnce()

    adapter = FacebookAdapter(
        session_id="test-session",
        chat_queue=queue,
    )

    payload = facebook_payload(
        message_id="retry-123",
        text="Paris",
    )

    websocket = FakeWebSocket(
        [payload, payload],
        adapter,
    )

    monkeypatch.setattr(
        facebook_module.websockets,
        "connect",
        lambda *args, **kwargs: FakeConnect(websocket),
    )

    adapter._running = True
    await adapter._listen_loop()

    assert adapter.dropped_messages == 1
    assert "retry-123" in adapter.seen_message_ids
    assert queue.qsize() == 1

    message = queue.get_nowait()

    assert message.message_id == "retry-123"
    assert message.text == "Paris"


@pytest.mark.asyncio
async def test_reconnects_after_connection_refused(monkeypatch):
    queue = asyncio.Queue(maxsize=10)

    adapter = FacebookAdapter(
        session_id="test-session",
        chat_queue=queue,
    )

    connect_calls = 0
    states = []

    def record_status(status):
        states.append(status.state.value)

    adapter.status_callback = record_status

    def fake_connect(*args, **kwargs):
        nonlocal connect_calls

        connect_calls += 1

        if connect_calls == 1:
            raise ConnectionRefusedError(
                111,
                "Connection refused",
            )

        websocket = FakeWebSocket([], adapter)
        return FakeConnect(websocket)

    async def fake_sleep(_delay):
        pass

    monkeypatch.setattr(
        facebook_module.websockets,
        "connect",
        fake_connect,
    )

    monkeypatch.setattr(
        facebook_module.asyncio,
        "sleep",
        fake_sleep,
    )

    adapter._running = True

    await adapter._listen_loop()

    assert connect_calls == 2
    assert adapter.status.reconnect_attempts == 1

    assert "DISCONNECTED" in states
    assert "CONNECTED" not in states


@pytest.mark.asyncio
async def test_invalid_json_is_ignored(monkeypatch):
    queue = asyncio.Queue(maxsize=10)

    adapter = FacebookAdapter(
        session_id="test-session",
        chat_queue=queue,
    )

    websocket = FakeWebSocket(
        [
            "this is not json",
            facebook_payload(
                message_id="valid-1",
                text="Paris",
            ),
        ],
        adapter,
    )

    monkeypatch.setattr(
        facebook_module.websockets,
        "connect",
        lambda *args, **kwargs: FakeConnect(websocket),
    )

    adapter._running = True
    await adapter._listen_loop()

    assert queue.qsize() == 1

    message = queue.get_nowait()

    assert message.text == "Paris"