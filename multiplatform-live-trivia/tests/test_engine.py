"""Trivia engine tests."""
import asyncio
import time

import pytest

from app.core.engine import TriviaEngine
from app.models import (
    ChatMessage,
    GameCommand,
    GameCommandType,
    GameState,
    Question,
)


def make_message(
    *,
    platform: str = "tiktok",
    user_id: str = "user",
    username: str = "User",
    text: str = "Paris",
    received_at: float = 100.0,
) -> ChatMessage:
    return ChatMessage(
        platform=platform,
        user_id=user_id,
        username=username,
        text=text,
        received_at=received_at,
    )


def make_engine(
    questions,
) -> TriviaEngine:
    return TriviaEngine(
        questions=questions,
        chat_queue=asyncio.Queue(maxsize=100),
        question_duration_sec=10.0,
        result_duration_sec=0.01,
        transition_duration_sec=0.01,
    )


def prepare_active_round(
    engine: TriviaEngine,
    *,
    start: float = 100.0,
    deadline: float = 110.0,
) -> None:
    engine.state = GameState.ACTIVE
    engine.current_question_index = 0
    engine.round_start_mono = start
    engine.current_deadline = deadline


def test_message_at_exact_deadline_is_rejected():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(
        engine,
        start=100.0,
        deadline=110.0,
    )

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            received_at=110.0,
        )
    )

    assert engine.get_snapshot().leaderboard == []


def test_message_before_round_start_is_rejected():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(
        engine,
        start=100.0,
        deadline=110.0,
    )

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            received_at=99.999,
        )
    )

    assert engine.get_snapshot().leaderboard == []
def test_message_at_exact_deadline_is_rejected():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(
        engine,
        start=100.0,
        deadline=110.0,
    )

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            received_at=110.0,
        )
    )

    assert engine.get_snapshot().leaderboard == []


def test_message_before_round_start_is_rejected():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(
        engine,
        start=100.0,
        deadline=110.0,
    )

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            received_at=99.999,
        )
    )

    assert engine.get_snapshot().leaderboard == []


@pytest.mark.asyncio
async def test_drain_processes_queued_eligible_message():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    engine._running = True
    engine.state = GameState.DRAINING
    engine.current_question_index = 0
    engine.round_start_mono = 100.0
    engine.current_deadline = 110.0

    await engine.chat_queue.put(
        make_message(
            user_id="u1",
            username="Alice",
            text="Paris",
            received_at=101.0,
        )
    )

    await engine._drain_eligible_queue()

    leaderboard = engine.get_snapshot().leaderboard

    assert len(leaderboard) == 1
    assert leaderboard[0]["display_name"] == "Alice"
    assert engine.chat_queue.empty()

def test_one_attempt_per_user_even_when_first_attempt_is_wrong():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(engine)

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            text="London",
            received_at=101.0,
        )
    )

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            text="Paris",
            received_at=102.0,
        )
    )

    assert engine.get_snapshot().leaderboard == []

def test_one_attempt_per_user_even_when_first_attempt_is_wrong():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(engine)

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            text="London",
            received_at=101.0,
        )
    )

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            text="Paris",
            received_at=102.0,
        )
    )

    assert engine.get_snapshot().leaderboard == []


def test_multiple_correct_users_can_score():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(engine)

    engine._handle_chat_message(
        make_message(
            user_id="u1",
            username="Alice",
            text="Paris",
            received_at=101.0,
        )
    )

    engine._handle_chat_message(
        make_message(
            user_id="u2",
            username="Bob",
            text="Paris",
            received_at=101.5,
        )
    )

    leaderboard = engine.get_snapshot().leaderboard

    assert len(leaderboard) == 2
    assert {
        entry["display_name"]
        for entry in leaderboard
    } == {"Alice", "Bob"}


def test_platform_is_part_of_user_identity():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    prepare_active_round(engine)

    engine._handle_chat_message(
        make_message(
            platform="tiktok",
            user_id="same-id",
            username="TikTok User",
            received_at=101.0,
        )
    )

    engine._handle_chat_message(
        make_message(
            platform="facebook",
            user_id="same-id",
            username="Facebook User",
            received_at=101.5,
        )
    )

    leaderboard = engine.get_snapshot().leaderboard

    assert len(leaderboard) == 2


@pytest.mark.asyncio
async def test_engine_requires_host_start():
    engine = TriviaEngine(
        questions=[
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ],
        chat_queue=asyncio.Queue(maxsize=100),
        question_duration_sec=0.05,
        result_duration_sec=0.01,
        transition_duration_sec=0.01,
    )

    engine.start()

    await asyncio.sleep(0.02)

    assert engine.state == GameState.WAITING_FOR_START

    await engine.stop()


@pytest.mark.asyncio
async def test_engine_first_correct_answer_scores():
    engine = TriviaEngine(
        questions=[
            Question(
                question="Capital of France?",
                answers=("Paris",),
            )
        ],
        chat_queue=asyncio.Queue(maxsize=100),
        question_duration_sec=1.0,
        result_duration_sec=0.01,
        transition_duration_sec=0.01,
    )

    engine.start()

    engine.handle_command(
        GameCommand(
            command=GameCommandType.START
        )
    )

    await asyncio.sleep(0.05)

    assert engine.state == GameState.ACTIVE

    now = time.monotonic()

    await engine.chat_queue.put(
        ChatMessage(
            platform="tiktok",
            user_id="user_1",
            username="Alice",
            text="Paris",
            received_at=now,
        )
    )

    await asyncio.sleep(0.05)

    leaderboard = engine.get_snapshot().leaderboard

    assert len(leaderboard) == 1
    assert leaderboard[0]["display_name"] == "Alice"
    assert leaderboard[0]["score"] == 1

    await engine.stop()

@pytest.mark.asyncio
async def test_drain_rejects_message_received_at_deadline():
    engine = make_engine(
        [
            Question(
                question="Capital?",
                answers=("Paris",),
            )
        ]
    )

    engine._running = True
    engine.state = GameState.DRAINING
    engine.current_question_index = 0
    engine.round_start_mono = 100.0
    engine.current_deadline = 110.0

    await engine.chat_queue.put(
        make_message(
            user_id="u1",
            username="Alice",
            text="Paris",
            received_at=110.0,
        )
    )

    await engine._drain_eligible_queue()

    assert engine.get_snapshot().leaderboard == []
    assert engine.chat_queue.empty()