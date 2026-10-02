"""Tests for Trivia Engine mechanics and state transitions."""
import asyncio
import time
import pytest

from app.core.engine import TriviaEngine
from app.models import ChatMessage, GameCommand, GameCommandType, GameState, Question


@pytest.mark.asyncio
async def test_engine_first_correct_answer_wins():
    questions = [
        Question(question="Capital of France?", answers=("Paris",)),
    ]
    chat_queue = asyncio.Queue(maxsize=100)

    engine = TriviaEngine(
        questions=questions,
        chat_queue=chat_queue,
        question_duration_sec=2.0,
        result_duration_sec=0.1,
        transition_duration_sec=0.1,
    )

    engine.start()

    # Issue START command to trigger state shift
    engine.handle_command(GameCommand(command=GameCommandType.START))
    await asyncio.sleep(0.1)

    assert engine.state == GameState.ACTIVE

    msg = ChatMessage(
        platform="tiktok",
        user_id="user_1",
        username="Alice",
        text="Paris",
        received_at=time.monotonic(),
    )
    await chat_queue.put(msg)

    await asyncio.sleep(0.2)

    snapshot = engine.get_snapshot()
    assert len(snapshot.leaderboard) == 1
    assert snapshot.leaderboard[0]["display_name"] == "Alice"
    assert snapshot.leaderboard[0]["score"] == 1

    await engine.stop()


@pytest.mark.asyncio
async def test_engine_ignores_late_messages():
    questions = [
        Question(question="Capital of France?", answers=("Paris",)),
    ]
    chat_queue = asyncio.Queue(maxsize=100)

    engine = TriviaEngine(
        questions=questions,
        chat_queue=chat_queue,
        question_duration_sec=0.2,
        result_duration_sec=0.1,
        transition_duration_sec=0.1,
    )

    engine.start()
    engine.handle_command(GameCommand(command=GameCommandType.START))
    await asyncio.sleep(0.05)

    late_time = time.monotonic() + 10.0
    msg = ChatMessage(
        platform="facebook",
        user_id="user_2",
        username="Bob",
        text="Paris",
        received_at=late_time,
    )
    await chat_queue.put(msg)
    await asyncio.sleep(0.3)

    snapshot = engine.get_snapshot()
    assert len(snapshot.leaderboard) == 0

    await engine.stop()