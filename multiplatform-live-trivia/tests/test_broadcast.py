"""Regression test: broadcasting must survive clients connecting mid-send."""
import asyncio

import pytest

pytest.importorskip("fastapi")

import main  # noqa: E402
from app.models import GameSnapshot  # noqa: E402


def make_snapshot():
    return GameSnapshot(
        state="ACTIVE", question_number=1, total_questions=1, question="Q",
        deadline=None, platforms={}, queue_size=0, queue_capacity=10,
        dropped_messages=0, leaderboard=[],
    )


class FakeSocket:
    def __init__(self, on_send=None, fail=False):
        self.on_send, self.fail, self.sent = on_send, fail, 0

    async def send_text(self, _payload):
        await asyncio.sleep(0)
        if self.on_send:
            self.on_send()
        if self.fail:
            raise RuntimeError("closed")
        self.sent += 1


@pytest.mark.asyncio
async def test_client_connecting_during_broadcast_does_not_crash():
    main.active_websockets.clear()
    late = FakeSocket()
    first = FakeSocket(on_send=lambda: main.active_websockets.add(late))
    main.active_websockets.add(first)

    await main.broadcast_snapshot(make_snapshot())  # used to raise RuntimeError

    assert first.sent == 1
    main.active_websockets.clear()


@pytest.mark.asyncio
async def test_dead_client_is_removed_and_others_still_receive():
    main.active_websockets.clear()
    dead, alive = FakeSocket(fail=True), FakeSocket()
    main.active_websockets.update({dead, alive})

    await main.broadcast_snapshot(make_snapshot())

    assert alive.sent == 1
    assert dead not in main.active_websockets
    main.active_websockets.clear()
