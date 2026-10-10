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
    
@pytest.mark.asyncio
async def test_multiple_questions_require_separate_start_and_finish():
    engine = TriviaEngine(
        questions=[
            Question(
                question="Capital of France?",
                answers=("Paris",),
            ),
            Question(
                question="Capital of Japan?",
                answers=("Tokyo",),
            ),
        ],
        chat_queue=asyncio.Queue(maxsize=100),
        question_duration_sec=0.05,
        result_duration_sec=0.01,
        transition_duration_sec=0.01,
        facebook_answer_grace_sec=0.0,
        drain_idle_sec=0.01,
        drain_hard_cap_sec=0.05,
    )

    async def wait_for_state(expected_state):
        deadline = time.monotonic() + 2.0

        while time.monotonic() < deadline:
            if engine.state == expected_state:
                return

            await asyncio.sleep(0.005)

        raise AssertionError(
            f"Timed out waiting for {expected_state}; "
            f"current state is {engine.state}"
        )

    engine.start()

    try:
        await wait_for_state(GameState.WAITING_FOR_START)
        assert engine.current_question_index == 0

        # Start question 1.
        engine.handle_command(
            GameCommand(command=GameCommandType.START)
        )

        await wait_for_state(GameState.ACTIVE)
        await wait_for_state(GameState.RESULT)

        # Results must stay visible until Space/START.
        assert engine.current_question_index == 0

        # Simulate pressing Space to advance.
        engine.handle_command(
            GameCommand(command=GameCommandType.START)
        )

        await wait_for_state(GameState.WAITING_FOR_START)
        assert engine.current_question_index == 1

        # Question 2 must wait for its own START.
        await asyncio.sleep(0.02)
        assert engine.state == GameState.WAITING_FOR_START

        engine.handle_command(
            GameCommand(command=GameCommandType.START)
        )

        await wait_for_state(GameState.ACTIVE)
        await wait_for_state(GameState.RESULT)

        assert engine.current_question_index == 1

        # Advance from the final result screen.
        engine.handle_command(
            GameCommand(command=GameCommandType.START)
        )

        await wait_for_state(GameState.FINISHED)
        assert engine.current_question_index == 2

    finally:
        await engine.stop()

@pytest.mark.asyncio
async def test_stop_during_active_round_shuts_down_cleanly():
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

    await asyncio.sleep(0.02)

    assert engine.state == GameState.ACTIVE
    assert engine._running is True

    await engine.stop()

    assert engine.state == GameState.STOPPED
    assert engine._running is False

    assert engine._engine_task is not None
    assert engine._engine_task.done()

    assert engine._queue_task is not None
    assert engine._queue_task.done()
@pytest.mark.asyncio
async def test_stop_while_waiting_for_start_shuts_down_cleanly():
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

    await asyncio.sleep(0.02)

    assert engine.state == GameState.WAITING_FOR_START
    assert engine._running is True

    await engine.stop()

    assert engine.state == GameState.STOPPED
    assert engine._running is False

    assert engine._engine_task is not None
    assert engine._engine_task.done()

    assert engine._queue_task is not None
    assert engine._queue_task.done()
    
@pytest.mark.asyncio
async def test_stop_during_transition_shuts_down_cleanly():
    engine = TriviaEngine(
        questions=[
            Question(
                question="Capital of France?",
                answers=("Paris",),
            ),
            Question(
                question="Capital of Japan?",
                answers=("Tokyo",),
            ),
        ],
        chat_queue=asyncio.Queue(maxsize=100),
        question_duration_sec=0.05,
        result_duration_sec=0.01,
        transition_duration_sec=0.2,
        facebook_answer_grace_sec=0.0,
        drain_idle_sec=0.01,
        drain_hard_cap_sec=0.05,
    )

    async def wait_for_state(expected_state):
        deadline = time.monotonic() + 2.0

        while time.monotonic() < deadline:
            if engine.state == expected_state:
                return

            await asyncio.sleep(0.005)

        raise AssertionError(
            f"Timed out waiting for {expected_state}; "
            f"current state is {engine.state}"
        )

    engine.start()

    try:
        # Start the first question.
        engine.handle_command(
            GameCommand(
                command=GameCommandType.START
            )
        )

        await wait_for_state(GameState.ACTIVE)
        await wait_for_state(GameState.RESULT)

        # Results now require a manual advance.
        engine.handle_command(
            GameCommand(
                command=GameCommandType.START
            )
        )

        await wait_for_state(GameState.TRANSITION)

        assert engine._running is True
        assert engine.current_question_index == 0

        # Stop the game while the transition is running.
        await engine.stop()

        assert engine.state == GameState.STOPPED
        assert engine._running is False
        assert engine._engine_task.done()
        assert engine._queue_task.done()

    finally:
        if engine._running:
            await engine.stop()

@pytest.mark.asyncio
async def test_facebook_grace_timer_counts_down():
    """The extra Facebook timer should decrease during DRAINING."""
    grace_sec = 0.5

    engine = TriviaEngine(
        questions=[
            Question(
                question="Capital of France?",
                answers=("Paris",),
            )
        ],
        chat_queue=asyncio.Queue(maxsize=100),
        question_duration_sec=1.0,
        facebook_answer_grace_sec=grace_sec,
    )

    # Simulate the original question timer having expired.
    engine.state = GameState.DRAINING
    engine.current_question_index = 0
    engine.current_deadline = time.monotonic() - 0.05

    first_snapshot = engine.get_snapshot()

    first_remaining = (
        first_snapshot.facebook_grace_time_remaining
    )

    assert first_remaining is not None
    assert 0 < first_remaining <= grace_sec
    assert (
        first_snapshot.facebook_grace_duration_sec
        == grace_sec
    )

    # Let some of the grace period pass.
    await asyncio.sleep(0.05)

    second_snapshot = engine.get_snapshot()

    second_remaining = (
        second_snapshot.facebook_grace_time_remaining
    )

    assert second_remaining is not None
    assert 0 < second_remaining < first_remaining


def test_facebook_grace_period_does_not_extend_tiktok():
    """Facebook gets extra answer time; TikTok does not."""
    engine = TriviaEngine(
        questions=[
            Question(
                question="Capital of France?",
                answers=("Paris",),
            )
        ],
        chat_queue=asyncio.Queue(maxsize=100),
        question_duration_sec=10.0,
        facebook_answer_grace_sec=3.0,
    )

    engine.state = GameState.DRAINING
    engine.current_question_index = 0
    engine.round_start_mono = 100.0
    engine.current_deadline = 110.0

    # This TikTok answer arrives after the normal deadline.
    engine._handle_chat_message(
        ChatMessage(
            platform="tiktok",
            user_id="tiktok-user",
            username="TikTok User",
            text="Paris",
            received_at=111.0,
        )
    )

    # TikTok must not receive a point during Facebook's grace period.
    assert engine.get_snapshot().leaderboard == []

    # This Facebook answer arrives during the extra three seconds.
    engine._handle_chat_message(
        ChatMessage(
            platform="facebook",
            user_id="facebook-user",
            username="Facebook User",
            text="Paris",
            received_at=111.0,
        )
    )

    leaderboard = engine.get_snapshot().leaderboard

    assert len(leaderboard) == 1
    assert leaderboard[0]["platform"] == "facebook"
    assert leaderboard[0]["display_name"] == "Facebook User"
    assert leaderboard[0]["score"] == 1

    # The Facebook grace deadline is exclusive.
    engine._handle_chat_message(
        ChatMessage(
            platform="facebook",
            user_id="too-late-user",
            username="Too Late",
            text="Paris",
            received_at=113.0,
        )
    )

    assert len(engine.get_snapshot().leaderboard) == 1