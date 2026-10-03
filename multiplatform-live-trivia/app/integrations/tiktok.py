
"""TikTok LIVE adapter."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from TikTokLive import TikTokLiveClient
from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent

try:
    from TikTokLive.client.errors import UserOfflineError
except ImportError:
    try:
        from TikTokLive.errors import UserOfflineError
    except ImportError:
        UserOfflineError = None

from app.models import (
    ChatMessage,
    Platform,
    PlatformStatus,
    PlatformStatusType,
)

logger = logging.getLogger(__name__)


class TikTokAdapter:
    def __init__(
        self,
        username: str,
        chat_queue: asyncio.Queue,
        status_callback=None,
    ) -> None:
        self.username = username.strip().lstrip("@")
        self.chat_queue = chat_queue
        self.status_callback = status_callback

        self.client = TikTokLiveClient(unique_id=self.username)

        self._is_running = False
        self._disconnect_event = asyncio.Event()
        self.dropped_messages = 0

        self.status = PlatformStatus(
            platform=Platform.TIKTOK.value,
            state=PlatformStatusType.DISABLED,
        )

        self.client.on(CommentEvent)(self._on_comment)
        self.client.on(ConnectEvent)(self._on_connect)
        self.client.on(DisconnectEvent)(self._on_disconnect)

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
                logger.exception("TikTok status callback failed.")

    async def _on_connect(self, event: Any) -> None:
        self._update_status(
            PlatformStatusType.CONNECTED,
            f"TikTok LIVE connected as @{self.username}",
        )

    async def _on_disconnect(self, event: Any) -> None:
        # Wake the connection manager so it can decide whether to reconnect.
        self._disconnect_event.set()

        if self._is_running:
            self._update_status(
                PlatformStatusType.DISCONNECTED,
                "TikTok LIVE disconnected",
            )

    async def _on_comment(self, event: Any) -> None:
        user = getattr(event, "user", None)

        user_id = (
            getattr(user, "user_id", None)
            or getattr(user, "unique_id", None)
            or "unknown"
        )

        username = (
            getattr(user, "nickname", None)
            or getattr(user, "unique_id", None)
            or str(user_id)
        )

        comment = getattr(event, "comment", "") or ""

        message_id = (
            getattr(event, "comment_id", None)
            or getattr(event, "id", None)
        )

        message = ChatMessage(
            platform=Platform.TIKTOK.value,
            user_id=str(user_id),
            username=str(username),
            text=str(comment),
            received_at=time.monotonic(),
            message_id=(
                str(message_id)
                if message_id is not None
                else None
            ),
        )

        try:
            self.chat_queue.put_nowait(message)
        except asyncio.QueueFull:
            self.dropped_messages += 1
            logger.warning(
                "TikTok chat queue full; dropped comment "
                "from %s (total dropped: %d)",
                message.username,
                self.dropped_messages,
            )

    async def start(self) -> None:
        if self._is_running:
            return

        if not self.username:
            self._update_status(
                PlatformStatusType.DISABLED,
                "TikTok username not configured",
            )
            return

        self._is_running = True

        try:
            while self._is_running:
                # Clear before starting so a disconnect during startup
                # cannot be lost.
                self._disconnect_event.clear()

                self._update_status(
                    PlatformStatusType.CONNECTING,
                    f"Connecting to @{self.username}",
                )

                try:
                    await self.client.start()

                    if not self._is_running:
                        break

                    # Some client versions return while the connection
                    # remains active. Do not start that client again.
                    if self.client.is_connected:
                        await self._disconnect_event.wait()

                    if not self._is_running:
                        break

                    logger.info(
                        "TikTok connection ended for @%s; "
                        "retrying in 5 seconds.",
                        self.username,
                    )
                    await asyncio.sleep(5)

                except asyncio.CancelledError:
                    raise

                except Exception as exc:
                    if not self._is_running:
                        break

                    is_offline_error = (
                        UserOfflineError is not None
                        and isinstance(exc, UserOfflineError)
                    ) or exc.__class__.__name__ == "UserOfflineError"

                    # If the client is still connected, wait for its
                    # disconnect event instead of calling start again.
                    if self.client.is_connected:
                        logger.warning(
                            "TikTok client for @%s is already connected; "
                            "waiting for disconnect before retrying.",
                            self.username,
                        )
                        await self._disconnect_event.wait()

                        if not self._is_running:
                            break

                        await asyncio.sleep(5)
                        continue

                    if is_offline_error:
                        retry_delay = 30
                        detail = "Broadcaster offline"
                        logger.info(
                            "TikTok broadcaster @%s is offline; "
                            "retrying in %d seconds.",
                            self.username,
                            retry_delay,
                        )
                    else:
                        retry_delay = 15
                        detail = str(exc)
                        logger.exception(
                            "TikTok connection failed for @%s",
                            self.username,
                        )

                    self._update_status(
                        PlatformStatusType.DISCONNECTED,
                        detail,
                    )

                    await asyncio.sleep(retry_delay)

        finally:
            self._is_running = False
            self._disconnect_event.set()

    async def stop(self) -> None:
        self._is_running = False
        self._disconnect_event.set()

        try:
            if self.client.is_connected:
                await self.client.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Error while disconnecting TikTok client."
            )
        finally:
            self._update_status(
                PlatformStatusType.DISABLED,
                "Stopped",
            )

