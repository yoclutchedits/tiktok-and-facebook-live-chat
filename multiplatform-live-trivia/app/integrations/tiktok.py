
"""TikTok LIVE adapter that converts comments into shared chat messages."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent

try:
    from TikTokLive.client.errors import UserOfflineError
except ImportError:
    try:
        from TikTokLive.errors import UserOfflineError
    except ImportError:
        # Compatibility fallback for TikTokLive versions that expose the
        # exception in a different module.
        UserOfflineError = None

from app.models import ChatMessage, Platform

logger = logging.getLogger(__name__)


class TikTokAdapter:
    """Receive TikTok comments and forward them to the shared chat queue."""

    def __init__(
        self,
        username: str,
        chat_queue: asyncio.Queue[ChatMessage],
    ) -> None:
        self.username = username.strip().lstrip("@")
        self.chat_queue = chat_queue
        self.client = TikTokLiveClient(unique_id=self.username)

        self._is_running = False
        self.dropped_messages = 0

        self.client.on(CommentEvent)(self._on_comment)

    async def _on_comment(self, event: Any) -> None:
        """Convert a TikTok comment event into the shared message model."""
        user = getattr(event, "user", None)

        raw_user_id = (
            getattr(user, "user_id", None)
            or getattr(user, "unique_id", None)
            or "unknown"
        )
        raw_username = (
            getattr(user, "nickname", None)
            or getattr(user, "unique_id", None)
            or str(raw_user_id)
        )

        comment_text = getattr(event, "comment", "") or ""

        raw_message_id = (
            getattr(event, "comment_id", None)
            or getattr(event, "id", None)
        )

        message = ChatMessage(
            platform=Platform.TIKTOK.value,
            user_id=str(raw_user_id),
            username=str(raw_username),
            text=str(comment_text),
            # The trivia engine uses monotonic time to check answer deadlines.
            received_at=time.monotonic(),
            message_id=(
                str(raw_message_id)
                if raw_message_id is not None
                else None
            ),
        )

        try:
            self.chat_queue.put_nowait(message)
        except asyncio.QueueFull:
            self.dropped_messages += 1
            logger.warning(
                "Chat queue full; dropped TikTok comment from %s "
                "(total dropped: %d)",
                message.username,
                self.dropped_messages,
            )

    async def start(self) -> None:
        """Connect to TikTok LIVE and reconnect after connection failures."""
        if not self.username:
            logger.info(
                "TikTok username is not configured; "
                "skipping TikTok integration."
            )
            return

        self._is_running = True
        logger.info(
            "Starting TikTok LIVE adapter for @%s",
            self.username,
        )

        while self._is_running:
            try:
                logger.info(
                    "Connecting to TikTok LIVE for @%s",
                    self.username,
                )
                await self.client.start()

                # Some client versions return when a stream disconnects.
                if self._is_running:
                    logger.warning(
                        "TikTok connection ended for @%s; reconnecting.",
                        self.username,
                    )
                    await asyncio.sleep(5)

            except asyncio.CancelledError:
                self._is_running = False
                raise

            except Exception as exc:
                if not self._is_running:
                    break

                is_offline_error = (
                    UserOfflineError is not None
                    and isinstance(exc, UserOfflineError)
                ) or exc.__class__.__name__ == "UserOfflineError"

                if is_offline_error:
                    retry_delay = 30
                    logger.warning(
                        "TikTok user @%s is offline. Retrying in %s seconds.",
                        self.username,
                        retry_delay,
                    )
                else:
                    retry_delay = 15
                    logger.exception(
                        "TikTok connection failed for @%s. "
                        "Retrying in %s seconds.",
                        self.username,
                        retry_delay,
                    )

                try:
                    await asyncio.sleep(retry_delay)
                except asyncio.CancelledError:
                    self._is_running = False
                    raise

    async def stop(self) -> None:
        """Stop reconnecting and disconnect the TikTok client."""
        self._is_running = False

        try:
            if self.client.is_connected:
                await self.client.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error while disconnecting TikTok client")
