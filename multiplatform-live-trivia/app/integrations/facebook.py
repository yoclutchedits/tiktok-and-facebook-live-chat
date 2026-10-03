
"""Facebook LIVE polling adapter."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
import time
from typing import Optional, Set

import httpx

from app.models import (
    ChatMessage,
    Platform,
    PlatformStatus,
    PlatformStatusType,
)

logger = logging.getLogger(__name__)


class FacebookAdapter:
    def __init__(
        self,
        access_token: str,
        live_video_id: str,
        chat_queue: asyncio.Queue,
        poll_interval_ms: int = 500,
        status_callback=None,
    ) -> None:
        self.access_token = access_token.strip()
        self.live_video_id = live_video_id.strip()
        self.chat_queue = chat_queue
        self.poll_interval = poll_interval_ms / 1000.0
        self.status_callback = status_callback

        self.status = PlatformStatus(
            platform=Platform.FACEBOOK.value,
            state=PlatformStatusType.DISABLED,
        )

        self.seen_message_ids: Set[str] = set()
        self._seen_order: deque[str] = deque(maxlen=5000)

        self._running = False
        self._task: Optional[asyncio.Task] = None

        self.dropped_messages = 0

    def _update_status(
        self,
        state: PlatformStatusType,
        detail: str = "",
    ) -> None:
        self.status.state = state
        self.status.detail = detail

        if self.status_callback is not None:
            try:
                self.status_callback(self.status)
            except Exception:
                logger.exception("Facebook status callback failed.")

    def _mark_seen(self, message_id: str) -> bool:
        if message_id in self.seen_message_ids:
            return False

        if len(self._seen_order) >= self._seen_order.maxlen:
            oldest = self._seen_order.popleft()
            self.seen_message_ids.discard(oldest)

        self._seen_order.append(message_id)
        self.seen_message_ids.add(message_id)

        return True

    async def start(self) -> None:
        if self._running:
            return

        if not self.access_token or not self.live_video_id:
            self._update_status(
                PlatformStatusType.DISABLED,
                "Missing access token or live_video_id",
            )
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        self._update_status(
            PlatformStatusType.CONNECTING,
            "Connecting to Facebook LIVE...",
        )

        url = (
            f"https://graph.facebook.com/v18.0/"
            f"{self.live_video_id}/comments"
        )

        params = {
            "access_token": self.access_token,
            "fields": "id,from,message,created_time",
            "order": "reverse_chronological",
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            while self._running:
                try:
                    response = await client.get(
                        url,
                        params=params,
                    )

                    if response.status_code != 200:
                        self.status.reconnect_attempts += 1

                        body = response.text[:500]

                        self._update_status(
                            PlatformStatusType.DISCONNECTED,
                            f"HTTP {response.status_code}: {body}",
                        )

                        await asyncio.sleep(self.poll_interval)
                        continue

                    self._update_status(
                        PlatformStatusType.CONNECTED,
                        f"Polling Video ID: {self.live_video_id}",
                    )

                    data = response.json().get("data", [])

                    # Process comments from oldest to newest.
                    for item in reversed(data):
                        message_id = item.get("id")

                        if not message_id:
                            continue

                        message_id = str(message_id)

                        # Skip comments already queued successfully.
                        # Do not mark a new comment as seen yet.
                        if message_id in self.seen_message_ids:
                            continue

                        user_data = item.get("from") or {}

                        user_id = str(
                            user_data.get("id") or "unknown"
                        )

                        username = str(
                            user_data.get("name") or user_id
                        )

                        text = str(item.get("message") or "")

                        created_at: Optional[datetime] = None
                        created_str = item.get("created_time")

                        if created_str:
                            try:
                                created_at = datetime.fromisoformat(
                                    created_str.replace("Z", "+00:00")
                                )

                                if created_at.tzinfo is None:
                                    created_at = created_at.replace(
                                        tzinfo=timezone.utc
                                    )

                            except ValueError:
                                logger.warning(
                                    "Could not parse Facebook created_time: %r",
                                    created_str,
                                )

                        # Capture receipt time when this comment is processed.
                        received_at = time.monotonic()

                        message = ChatMessage(
                            platform=Platform.FACEBOOK.value,
                            user_id=user_id,
                            username=username,
                            text=text,
                            received_at=received_at,
                            message_id=message_id,
                            platform_created_at=created_at,
                        )

                        try:
                            # Enqueue first. Mark as seen only if enqueue
                            # succeeds, allowing full-queue comments to retry.
                            self.chat_queue.put_nowait(message)
                            self._mark_seen(message_id)

                        except asyncio.QueueFull:
                            self.dropped_messages += 1

                            logger.warning(
                                "Facebook chat queue full; comment %s "
                                "will be retried on a later poll "
                                "(total queue-full events: %d)",
                                message_id,
                                self.dropped_messages,
                            )

                except asyncio.CancelledError:
                    raise

                except Exception:
                    self.status.reconnect_attempts += 1

                    self._update_status(
                        PlatformStatusType.DISCONNECTED,
                        "Polling error",
                    )

                    logger.exception(
                        "Error polling Facebook LIVE comments."
                    )

                await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        self._running = False

        if self._task is not None:
            self._task.cancel()

            try:
                await self._task
            except asyncio.CancelledError:
                pass

            self._task = None

        self._update_status(
            PlatformStatusType.DISABLED,
            "Stopped",
        )