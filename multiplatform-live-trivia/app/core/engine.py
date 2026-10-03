"""Canonical trivia game engine and state machine."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Set

from app.core.leaderboard import LeaderboardManager
from app.core.matcher import match_answer
from app.models import (
    ChatMessage,
    GameCommand,
    GameCommandType,
    GameSnapshot,
    GameState,
    Platform,
    Question,
)

logger = logging.getLogger(__name__)


class TriviaEngine:
    """
    Single source of truth for trivia game state.

    Rules:
    - 10-second question window by default
    - strict half-open answer window: start <= received_at < deadline
    - one attempt per platform/user per question
    - multiple users may score on the same question
    - every question requires a separate host START
    - queued messages receive a short DRAINING grace period
    """

    def __init__(
        self,
        questions: list[Question],
        chat_queue: asyncio.Queue,
        question_duration_sec: float = 10.0,
        result_duration_sec: float = 3.0,
        transition_duration_sec: float = 3.0,
        snapshot_callback: Optional[Callable[[GameSnapshot], None]] = None,
        fb_tolerance_sec: float = 1.0,
        drain_idle_sec: float = 0.05,
        drain_hard_cap_sec: float = 1.5,
    ) -> None:
        self.questions = questions
        self.chat_queue = chat_queue

        self.question_duration_sec = float(question_duration_sec)
        self.result_duration_sec = float(result_duration_sec)
        self.transition_duration_sec = float(transition_duration_sec)
        self.fb_tolerance_sec = float(fb_tolerance_sec)
        self.drain_idle_sec = float(drain_idle_sec)
        self.drain_hard_cap_sec = float(drain_hard_cap_sec)

        self.snapshot_callback = snapshot_callback

        self.state = GameState.IDLE
        self.current_question_index = 0

        self.leaderboard_mgr = LeaderboardManager()

        self.round_start_mono: Optional[float] = None
        self.round_start_wall_utc: Optional[datetime] = None
        self.current_deadline: Optional[float] = None

        # First correct responder retained for UI/backward compatibility.
        # It does NOT terminate the question.
        self.question_winner: Optional[dict] = None

        # A user gets exactly one attempt per question.
        # Platform is part of the identity.
        self.answered_users_this_question: Set[str] = set()

        self._engine_task: Optional[asyncio.Task] = None
        self._queue_task: Optional[asyncio.Task] = None
        self._running = False
        self._start_event = asyncio.Event()

        self.dropped_messages = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start engine background tasks inside an active asyncio loop."""
        if self._running:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "TriviaEngine.start() must be called inside a running asyncio loop."
            ) from exc

        self._running = True
        self.state = GameState.WAITING_FOR_START

        self._engine_task = loop.create_task(self._run_game_loop())
        self._queue_task = loop.create_task(self._process_queue_loop())

        self._notify_snapshot()

    async def stop(self) -> None:
        """Stop the engine and its workers cleanly."""
        self._running = False
        self.state = GameState.STOPPED
        self._start_event.set()

        current_task = asyncio.current_task()

        tasks = [
            task
            for task in (self._engine_task, self._queue_task)
            if task is not None
            and task is not current_task
            and not task.done()
        ]

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._notify_snapshot()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def handle_command(self, cmd: GameCommand) -> None:
        """Handle host START/STOP commands."""
        if cmd.command == GameCommandType.START:
            if self.state == GameState.WAITING_FOR_START and self._running:
                self._start_event.set()
                logger.info("Host START received.")
            return

        if cmd.command == GameCommandType.STOP:
            if not self._running:
                return

            try:
                asyncio.get_running_loop().create_task(self.stop())
            except RuntimeError:
                logger.warning("STOP received without a running event loop.")

    # ------------------------------------------------------------------
    # Game loop
    # ------------------------------------------------------------------

    async def _run_game_loop(self) -> None:
        try:
            while self._running and self.current_question_index < len(self.questions):
                self.state = GameState.WAITING_FOR_START
                self.current_deadline = None
                self._notify_snapshot()

                await self._start_event.wait()
                self._start_event.clear()

                if not self._running:
                    break

                question = self.questions[self.current_question_index]
                await self._run_question_cycle(question)

                if not self._running:
                    break

                self.current_question_index += 1

            if self._running:
                self.state = GameState.FINISHED
                self.current_deadline = None
                self._notify_snapshot()

        except asyncio.CancelledError:
            pass

        except Exception:
            logger.exception("Unexpected error in trivia game loop.")
            self._running = False
            self.state = GameState.STOPPED
            self._notify_snapshot()

    async def _run_question_cycle(self, question: Question) -> None:
        self.state = GameState.ACTIVE
        self.question_winner = None
        self.answered_users_this_question.clear()

        self.round_start_mono = time.monotonic()
        self.round_start_wall_utc = datetime.now(timezone.utc)
        self.current_deadline = (
            self.round_start_mono + self.question_duration_sec
        )

        self._notify_snapshot()

        while self._running:
            now = time.monotonic()

            if now >= self.current_deadline:
                break

            await asyncio.sleep(
                min(
                    0.02,
                    max(0.0, self.current_deadline - time.monotonic()),
                )
            )

        if not self._running:
            return

        self.state = GameState.DRAINING
        self._notify_snapshot()

        await self._drain_eligible_queue()

        if not self._running:
            return

        self.state = GameState.RESULT
        self._notify_snapshot()

        await asyncio.sleep(self.result_duration_sec)

        if not self._running:
            return

        if self.current_question_index < len(self.questions) - 1:
            self.state = GameState.TRANSITION
            self._notify_snapshot()

            await asyncio.sleep(self.transition_duration_sec)

    # ------------------------------------------------------------------
    # Queue handling
    # ------------------------------------------------------------------

    async def _process_queue_loop(self) -> None:
        try:
            while self._running:
                try:
                    msg: ChatMessage = await asyncio.wait_for(
                        self.chat_queue.get(),
                        timeout=0.05,
                    )
                except asyncio.TimeoutError:
                    continue

                try:
                    self._handle_chat_message(msg)
                except Exception:
                    logger.exception("Failed to process chat message.")
                finally:
                    self.chat_queue.task_done()

        except asyncio.CancelledError:
            pass

    async def _drain_eligible_queue(self) -> None:
        """
        Give already-queued messages a short grace period to be processed.

        Actual eligibility is still decided by ChatMessage.received_at,
        not by the moment the queue worker happens to process the message.
        """
        idle_since: Optional[float] = None
        drain_started = time.monotonic()

        while self._running:
            now = time.monotonic()

            if now - drain_started >= self.drain_hard_cap_sec:
                break

            if self.chat_queue.empty():
                if idle_since is None:
                    idle_since = now
                elif now - idle_since >= self.drain_idle_sec:
                    break
            else:
                idle_since = None

            await asyncio.sleep(0.01)

    # ------------------------------------------------------------------
    # Message evaluation
    # ------------------------------------------------------------------

    def _handle_chat_message(self, msg: ChatMessage) -> None:
        if self.state not in (GameState.ACTIVE, GameState.DRAINING):
            return

        if self.round_start_mono is None:
            return

        if self.current_deadline is None:
            return

        # Strict half-open window:
        # start INCLUDED
        # deadline EXCLUDED
        if not (
            self.round_start_mono
            <= msg.received_at
            < self.current_deadline
        ):
            return

        platform = (
            msg.platform.value
            if isinstance(msg.platform, Platform)
            else str(msg.platform).lower()
        )

        # Facebook also gives us an authoritative platform timestamp.
        # Allow a small tolerance around the round start for clock/delivery
        # differences, but reject clearly old comments.
        if (
            platform == Platform.FACEBOOK.value
            and msg.platform_created_at is not None
            and self.round_start_wall_utc is not None
        ):
            created_at = msg.platform_created_at

            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            earliest_allowed = (
                self.round_start_wall_utc.timestamp()
                - self.fb_tolerance_sec
            )

            if created_at.timestamp() < earliest_allowed:
                return

        user_key = f"{platform}:{msg.user_id}"

        # One attempt only, even when the first answer was wrong.
        if user_key in self.answered_users_this_question:
            return

        self.answered_users_this_question.add(user_key)

        if not self.questions:
            return

        if self.current_question_index >= len(self.questions):
            return

        question = self.questions[self.current_question_index]

        explicit_type = (
            question.answer_type.name
            if question.answer_type is not None
            else None
        )

        is_correct = match_answer(
            user_input=msg.text,
            expected_answers=question.answers,
            explicit_type=explicit_type,
        )

        if not is_correct:
            return

        player = self.leaderboard_mgr.record_score(
            platform=platform,
            user_id=msg.user_id,
            display_name=msg.username,
            timestamp=msg.received_at,
        )

        if self.question_winner is None:
            self.question_winner = {
                "player_key": player.player_key,
                "display_name": player.display_name,
                "platform": player.platform,
            }

        logger.info(
            "Question %s: correct answer by %s (%s).",
            self.current_question_index + 1,
            player.display_name,
            platform,
        )

        self._notify_snapshot()

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def get_snapshot(self) -> GameSnapshot:
        current_question = None
        correct_answer = None

        if 0 <= self.current_question_index < len(self.questions):
            q = self.questions[self.current_question_index]
            current_question = q.question

            if self.state in (
                GameState.RESULT,
                GameState.FINISHED,
            ) and q.answers:
                correct_answer = q.answers[0]

        time_remaining = None

        if self.state in (
            GameState.ACTIVE,
            GameState.DRAINING,
        ) and self.current_deadline is not None:
            time_remaining = max(
                0.0,
                self.current_deadline - time.monotonic(),
            )

        return GameSnapshot(
            state=self.state.value,
            question_number=(
                min(
                    self.current_question_index + 1,
                    len(self.questions),
                )
                if self.questions
                else 0
            ),
            total_questions=len(self.questions),
            question=current_question,
            deadline=self.current_deadline,
            platforms={},
            queue_size=self.chat_queue.qsize(),
            queue_capacity=getattr(self.chat_queue, "maxsize", 0),
            dropped_messages=self.dropped_messages,
            leaderboard=self.leaderboard_mgr.get_leaderboard(),
            correct_answer=correct_answer,
            time_remaining=time_remaining,
        )

    def _notify_snapshot(self) -> None:
        if self.snapshot_callback is None:
            return

        try:
            self.snapshot_callback(self.get_snapshot())
        except Exception:
            logger.exception("Snapshot callback failed.")