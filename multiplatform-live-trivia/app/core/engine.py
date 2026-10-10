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
    QuestionType,
)

logger = logging.getLogger(__name__)


class TriviaEngine:
    """
    Single source of truth for trivia game state.

    Rules:
    - The normal question timer is controlled by the backend.
    - TikTok answers must arrive before the normal deadline.
    - Facebook may use an additional configured answer grace period.
    - Each user gets one attempt per platform per question.
    - Multiple users can score on the same question.
    - Each timed question requires a separate host START.
    - Results remain visible until the host presses Space.
    - Welcome entries have no answer or countdown timers.
    """

    def __init__(
        self,
        questions: list[Question],
        chat_queue: asyncio.Queue,
        question_duration_sec: float = 10.0,
        result_duration_sec: float = 3.0,
        transition_duration_sec: float = 3.0,
        snapshot_callback: Optional[
            Callable[[GameSnapshot], None]
        ] = None,
        fb_tolerance_sec: float = 1.0,
        drain_idle_sec: float = 0.05,
        drain_hard_cap_sec: float = 1.5,
        facebook_answer_grace_sec: float = 0.0,
    ) -> None:
        self.questions = questions
        self.chat_queue = chat_queue

        self.question_duration_sec = max(
            0.0,
            float(question_duration_sec),
        )

        # Retained for compatibility with the existing configuration.
        # Results now wait for manual advancement.
        self.result_duration_sec = max(
            0.0,
            float(result_duration_sec),
        )

        self.transition_duration_sec = max(
            0.0,
            float(transition_duration_sec),
        )

        self.fb_tolerance_sec = max(
            0.0,
            float(fb_tolerance_sec),
        )

        self.facebook_answer_grace_sec = max(
            0.0,
            float(facebook_answer_grace_sec),
        )

        self.drain_idle_sec = max(
            0.0,
            float(drain_idle_sec),
        )

        self.drain_hard_cap_sec = max(
            0.0,
            float(drain_hard_cap_sec),
        )

        self.snapshot_callback = snapshot_callback

        self.state = GameState.IDLE
        self.current_question_index = 0

        self.leaderboard_mgr = LeaderboardManager()

        self.round_start_mono: Optional[float] = None
        self.round_start_wall_utc: Optional[datetime] = None
        self.current_deadline: Optional[float] = None

        # First correct responder, retained for UI compatibility.
        # This does not terminate a question.
        self.question_winner: Optional[dict] = None

        # One attempt per platform/user per question.
        self.answered_users_this_question: Set[str] = set()

        self._engine_task: Optional[asyncio.Task] = None
        self._queue_task: Optional[asyncio.Task] = None

        self._running = False

        # Starts questions and dismisses a welcome screen.
        self._start_event = asyncio.Event()

        # Advances from RESULT when the host presses Space.
        self._advance_event = asyncio.Event()

        self.dropped_messages = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start background tasks inside an active asyncio loop."""
        if self._running:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "TriviaEngine.start() must be called "
                "inside a running asyncio loop."
            ) from exc

        self._running = True
        self.state = GameState.WAITING_FOR_START

        self._engine_task = loop.create_task(
            self._run_game_loop()
        )

        self._queue_task = loop.create_task(
            self._process_queue_loop()
        )

        self._notify_snapshot()

    async def stop(self) -> None:
        """Stop the engine and its workers cleanly."""
        self._running = False
        self.state = GameState.STOPPED

        # Wake any task waiting for a host action.
        self._start_event.set()
        self._advance_event.set()

        current_task = asyncio.current_task()

        tasks = [
            task
            for task in (
                self._engine_task,
                self._queue_task,
            )
            if task is not None
            and task is not current_task
            and not task.done()
        ]

        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(
                *tasks,
                return_exceptions=True,
            )

        self._notify_snapshot()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def handle_command(self, cmd: GameCommand) -> None:
        """Handle host START/STOP commands."""
        if cmd.command == GameCommandType.START:
            if not self._running:
                return

            if self.state == GameState.WAITING_FOR_START:
                self._start_event.set()

                logger.info(
                    "Host START received."
                )

            elif self.state == GameState.RESULT:
                self._advance_event.set()

                logger.info(
                    "Host advanced from the result screen."
                )

            return

        if cmd.command == GameCommandType.STOP:
            if not self._running:
                return

            try:
                asyncio.get_running_loop().create_task(
                    self.stop()
                )
            except RuntimeError:
                logger.warning(
                    "STOP received without a running event loop."
                )

    # ------------------------------------------------------------------
    # Game loop
    # ------------------------------------------------------------------

    async def _run_game_loop(self) -> None:
        try:
            while (
                self._running
                and self.current_question_index < len(self.questions)
            ):
                self.state = GameState.WAITING_FOR_START
                self.current_deadline = None

                self._notify_snapshot()

                # Wait for the host to start or dismiss the current screen.
                await self._start_event.wait()
                self._start_event.clear()

                if not self._running:
                    break

                question = self.questions[
                    self.current_question_index
                ]

                # ------------------------------------------------------
                # Welcome screen
                #
                # It has no question timer, answer window, Facebook
                # grace period, or result phase.
                #
                # It is displayed while WAITING_FOR_START. One Space
                # press dismisses it and moves to the following entry.
                # ------------------------------------------------------

                if question.question_type == QuestionType.WELCOME:
                    logger.info(
                        "Welcome screen dismissed by host."
                    )

                    self.current_question_index += 1
                    continue

                # ------------------------------------------------------
                # Normal timed question
                # ------------------------------------------------------

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
            logger.exception(
                "Unexpected error in trivia game loop."
            )

            self._running = False
            self.state = GameState.STOPPED

            self._notify_snapshot()

    async def _run_question_cycle(
        self,
        question: Question,
    ) -> None:
        """Run the active question, drain answers, then wait for Space."""
        self.state = GameState.ACTIVE

        self.question_winner = None
        self.answered_users_this_question.clear()

        self.round_start_mono = time.monotonic()
        self.round_start_wall_utc = datetime.now(
            timezone.utc
        )

        self.current_deadline = (
            self.round_start_mono
            + self.question_duration_sec
        )

        self._notify_snapshot()

        # --------------------------------------------------------------
        # Original question timer
        # --------------------------------------------------------------

        while self._running:
            now = time.monotonic()

            if now >= self.current_deadline:
                break

            await asyncio.sleep(
                min(
                    0.02,
                    max(
                        0.0,
                        self.current_deadline
                        - time.monotonic(),
                    ),
                )
            )

        if not self._running:
            return

        # --------------------------------------------------------------
        # Drain queued messages and allow the Facebook grace period
        # --------------------------------------------------------------

        self.state = GameState.DRAINING
        self._notify_snapshot()

        await self._drain_eligible_queue()

        if not self._running:
            return

        # Clear before publishing RESULT, preventing a previous event
        # from advancing this result immediately.
        self._advance_event.clear()

        # --------------------------------------------------------------
        # Result screen: wait indefinitely for the host to press Space
        # --------------------------------------------------------------

        self.state = GameState.RESULT
        self._notify_snapshot()

        await self._advance_event.wait()
        self._advance_event.clear()

        if not self._running:
            return

        # --------------------------------------------------------------
        # Transition to the next entry
        # --------------------------------------------------------------

        if (
            self.current_question_index
            < len(self.questions) - 1
        ):
            self.state = GameState.TRANSITION
            self._notify_snapshot()

            await asyncio.sleep(
                self.transition_duration_sec
            )

    # ------------------------------------------------------------------
    # Queue handling
    # ------------------------------------------------------------------

    async def _process_queue_loop(self) -> None:
        """
        Process incoming chat messages outside DRAINING.

        During DRAINING this worker pauses so the drain worker has
        exclusive responsibility for reading the shared queue.
        """
        try:
            while self._running:
                if self.state == GameState.DRAINING:
                    await asyncio.sleep(0.01)
                    continue

                try:
                    msg: ChatMessage = (
                        self.chat_queue.get_nowait()
                    )

                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.01)
                    continue

                try:
                    self._handle_chat_message(msg)

                except Exception:
                    logger.exception(
                        "Failed to process chat message."
                    )

                finally:
                    self.chat_queue.task_done()

        except asyncio.CancelledError:
            pass

    async def _drain_eligible_queue(self) -> None:
        """
        Drain messages during DRAINING.

        TikTok retains its original question deadline. Facebook may
        submit messages received before:

            question deadline + facebook_answer_grace_sec

        The drain waits through the grace period even if the queue is
        temporarily empty, then waits for a short idle interval.
        """
        drain_started = time.monotonic()

        question_deadline = (
            self.current_deadline
            if self.current_deadline is not None
            else drain_started
        )

        facebook_grace_deadline = (
            question_deadline
            + self.facebook_answer_grace_sec
        )

        # Preserve the full Facebook grace period and give queued
        # messages a bounded additional time to drain.
        hard_deadline = (
            max(
                drain_started,
                facebook_grace_deadline,
            )
            + self.drain_hard_cap_sec
        )

        empty_since: Optional[float] = None

        while self._running:
            now = time.monotonic()

            if now >= hard_deadline:
                break

            try:
                msg: ChatMessage = (
                    self.chat_queue.get_nowait()
                )

            except asyncio.QueueEmpty:
                now = time.monotonic()

                # Always wait through the configured grace period.
                if now < facebook_grace_deadline:
                    empty_since = None

                    await asyncio.sleep(
                        min(
                            0.01,
                            facebook_grace_deadline - now,
                        )
                    )

                    continue

                # After the grace period, wait until the queue has
                # remained empty for the configured idle interval.
                if empty_since is None:
                    empty_since = now

                elif (
                    now - empty_since
                    >= self.drain_idle_sec
                ):
                    break

                remaining_idle = max(
                    0.001,
                    self.drain_idle_sec
                    - (now - empty_since),
                )

                await asyncio.sleep(
                    min(
                        0.01,
                        remaining_idle,
                        max(
                            0.001,
                            hard_deadline - now,
                        ),
                    )
                )

                continue

            empty_since = None

            try:
                self._handle_chat_message(msg)

            except Exception:
                logger.exception(
                    "Failed to process chat message during drain."
                )

            finally:
                self.chat_queue.task_done()

    # ------------------------------------------------------------------
    # Message evaluation
    # ------------------------------------------------------------------

    def _handle_chat_message(
        self,
        msg: ChatMessage,
    ) -> None:
        """Validate a chat message and score a correct answer."""
        if self.state not in (
            GameState.ACTIVE,
            GameState.DRAINING,
        ):
            return

        if self.round_start_mono is None:
            return

        if self.current_deadline is None:
            return

        platform = (
            msg.platform.value
            if isinstance(msg.platform, Platform)
            else str(msg.platform).lower()
        )

        # --------------------------------------------------------------
        # Platform-specific answer deadline
        # --------------------------------------------------------------

        message_deadline = self.current_deadline

        if platform == Platform.FACEBOOK.value:
            message_deadline += (
                self.facebook_answer_grace_sec
            )

        # Inclusive start, exclusive deadline.
        if not (
            self.round_start_mono
            <= msg.received_at
            < message_deadline
        ):
            return

        # --------------------------------------------------------------
        # Facebook authoritative platform timestamp check
        # --------------------------------------------------------------

        if (
            platform == Platform.FACEBOOK.value
            and msg.platform_created_at is not None
            and self.round_start_wall_utc is not None
        ):
            created_at = msg.platform_created_at

            if created_at.tzinfo is None:
                created_at = created_at.replace(
                    tzinfo=timezone.utc
                )

            earliest_allowed = (
                self.round_start_wall_utc.timestamp()
                - self.fb_tolerance_sec
            )

            if created_at.timestamp() < earliest_allowed:
                return

        # --------------------------------------------------------------
        # One attempt per user/platform per question
        # --------------------------------------------------------------

        user_key = f"{platform}:{msg.user_id}"

        if user_key in self.answered_users_this_question:
            return

        self.answered_users_this_question.add(user_key)

        if not self.questions:
            return

        if self.current_question_index >= len(self.questions):
            return

        question = self.questions[
            self.current_question_index
        ]

        # Welcome entries cannot score.
        if question.question_type == QuestionType.WELCOME:
            return

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

        # --------------------------------------------------------------
        # Award the point
        # --------------------------------------------------------------

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
        """Build the current public game snapshot."""
        current_question = None
        correct_answer = None
        options = None
        question_type = QuestionType.QUESTION.value

        if (
            0 <= self.current_question_index
            < len(self.questions)
        ):
            question = self.questions[
                self.current_question_index
            ]

            current_question = question.question
            question_type = question.question_type.value

            if question.options:
                options = dict(question.options)

            if (
                self.state
                in (
                    GameState.RESULT,
                    GameState.FINISHED,
                )
                and question.answers
            ):
                correct_answer = question.answers[0]

        # Original question timer.
        time_remaining = None

        if (
            self.state
            in (
                GameState.ACTIVE,
                GameState.DRAINING,
            )
            and self.current_deadline is not None
        ):
            time_remaining = max(
                0.0,
                self.current_deadline - time.monotonic(),
            )

        # Facebook extra timer, visible only during DRAINING.
        facebook_grace_time_remaining = None

        if (
            self.state == GameState.DRAINING
            and self.current_deadline is not None
        ):
            facebook_grace_time_remaining = max(
                0.0,
                (
                    self.current_deadline
                    + self.facebook_answer_grace_sec
                    - time.monotonic()
                ),
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
            queue_capacity=getattr(
                self.chat_queue,
                "maxsize",
                0,
            ),
            dropped_messages=self.dropped_messages,
            leaderboard=self.leaderboard_mgr.get_leaderboard(),
            correct_answer=correct_answer,
            time_remaining=time_remaining,
            options=options,
            facebook_grace_time_remaining=(
                facebook_grace_time_remaining
            ),
            facebook_grace_duration_sec=(
                self.facebook_answer_grace_sec
            ),
            question_type=question_type,
        )

    def _notify_snapshot(self) -> None:
        """Notify the optional snapshot callback."""
        if self.snapshot_callback is None:
            return

        try:
            self.snapshot_callback(self.get_snapshot())

        except Exception:
            logger.exception(
                "Snapshot callback failed."
            )