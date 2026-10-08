"""Facebook LIVE chat adapter using Social Stream Ninja."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import time
from typing import Any, Optional, Set

import websockets

from app.models import (
    ChatMessage,
    Platform,
    PlatformStatus,
    PlatformStatusType,
)

logger = logging.getLogger(__name__)


class FacebookAdapter:
    """
    Receives Facebook LIVE chat through Social Stream Ninja.

    Social Stream Ninja sends chat messages over WebSocket channel 4.

    IMPORTANT:
    A successful WebSocket connection does NOT necessarily mean that
    the Social Stream Ninja extension is active or that Facebook chat
    is being captured.

    Therefore Facebook is only marked CONNECTED after a valid Facebook
    chat message is actually received.
    """

    # Maximum amount of time we allow the WebSocket to remain silent
    # before considering the Facebook connection inactive.
    NO_DATA_TIMEOUT_SEC = 15.0

    def __init__(
        self,
        session_id: str,
        chat_queue: asyncio.Queue,
        status_callback=None,
    ) -> None:
        self.session_id = session_id.strip()
        self.chat_queue = chat_queue
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

        # True only after an actual Facebook chat message has arrived.
        self._received_facebook_data = False

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
                logger.exception(
                    "Facebook status callback failed."
                )

    def _mark_seen(self, message_id: str) -> bool:
        """
        Record a message ID as seen.

        Returns False if the ID has already been seen.
        """
        if message_id in self.seen_message_ids:
            return False

        if len(self._seen_order) >= self._seen_order.maxlen:
            oldest = self._seen_order.popleft()
            self.seen_message_ids.discard(oldest)

        self._seen_order.append(message_id)
        self.seen_message_ids.add(message_id)

        return True

    @staticmethod
    def _unwrap_message(
        data: Any,
    ) -> dict[str, Any] | None:
        """
        Social Stream Ninja payloads can be wrapped.

        Try common wrapper shapes and return the actual
        message object.
        """
        if not isinstance(data, dict):
            return None

        if (
            "chatname" in data
            or "chatmessage" in data
            or "userid" in data
        ):
            return data

        for key in (
            "data",
            "message",
            "value",
            "payload",
        ):
            nested = data.get(key)

            if isinstance(nested, dict):
                result = FacebookAdapter._unwrap_message(
                    nested
                )

                if result is not None:
                    return result

            elif isinstance(nested, str):
                try:
                    decoded = json.loads(nested)
                except (TypeError, ValueError):
                    continue

                result = FacebookAdapter._unwrap_message(
                    decoded
                )

                if result is not None:
                    return result

        return None

    @staticmethod
    def _extract_text(
        data: dict[str, Any],
    ) -> str:
        """
        Prefer plain text metadata when Social Stream Ninja
        provides it.

        chatmessage may contain HTML/emote markup.
        """
        meta = data.get("meta")

        if isinstance(meta, dict):
            plain_text = meta.get("plainText")

            if plain_text is not None:
                return str(plain_text).strip()

        return str(
            data.get("chatmessage")
            or data.get("message")
            or ""
        ).strip()

    async def start(self) -> None:
        """Start the Facebook chat listener."""
        if self._running:
            return

        if not self.session_id:
            self._update_status(
                PlatformStatusType.DISABLED,
                "Social Stream Ninja session_id not configured",
            )
            return

        self._running = True

        self._task = asyncio.create_task(
            self._listen_loop()
        )

    async def _listen_loop(self) -> None:
        """
        Connect to Social Stream Ninja Channel 4 and
        continuously receive Facebook chat messages.

        The WebSocket opening is NOT considered a successful
        Facebook connection. We only report CONNECTED after
        receiving an actual Facebook chat message.
        """
        uri = (
            "wss://io.socialstream.ninja/join/"
            f"{self.session_id}/4"
        )

        while self._running:
            self._received_facebook_data = False

            try:
                self._update_status(
                    PlatformStatusType.CONNECTING,
                    "Connecting to Social Stream Ninja...",
                )

                async with websockets.connect(
                    uri,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as websocket:

                    if not self._running:
                        break

                    logger.info(
                        "Facebook WebSocket connected through "
                        "Social Stream Ninja session %s. "
                        "Waiting for Facebook chat data.",
                        self.session_id,
                    )

                    while self._running:
                        try:
                            raw_message = await asyncio.wait_for(
                                websocket.recv(),
                                timeout=self.NO_DATA_TIMEOUT_SEC,
                            )

                        except asyncio.TimeoutError:
                            if self._received_facebook_data:
                                self._update_status(
                                    PlatformStatusType.DISCONNECTED,
                                    "Facebook chat stopped sending data",
                                )
                            else:
                                self._update_status(
                                    PlatformStatusType.DISCONNECTED,
                                    "No Facebook chat data received; "
                                    "check the Social Stream Ninja extension "
                                    "and session ID",
                                )

                            logger.warning(
                                "No Facebook chat data received from "
                                "Social Stream Ninja for %.1f seconds.",
                                self.NO_DATA_TIMEOUT_SEC,
                            )

                            break

                        if not self._running:
                            break

                        # --------------------------------------------------
                        # Decode JSON
                        # --------------------------------------------------

                        try:
                            data = json.loads(
                                raw_message
                            )
                        except (
                            TypeError,
                            ValueError,
                        ):
                            logger.warning(
                                "Ignoring invalid Social Stream "
                                "JSON message."
                            )
                            continue

                        # --------------------------------------------------
                        # Unwrap message
                        # --------------------------------------------------

                        message_data = (
                            self._unwrap_message(data)
                        )

                        if message_data is None:
                            continue

                        # --------------------------------------------------
                        # Only accept Facebook messages
                        # --------------------------------------------------

                        source_type = str(
                            message_data.get("type")
                            or message_data.get("platform")
                            or ""
                        ).strip().lower()

                        if (
                            source_type
                            and source_type != "facebook"
                        ):
                            continue

                        # --------------------------------------------------
                        # Extract comment text
                        # --------------------------------------------------

                        text = self._extract_text(
                            message_data
                        )

                        if not text:
                            continue

                        # --------------------------------------------------
                        # We now know Facebook chat is actually working.
                        # --------------------------------------------------

                        if not self._received_facebook_data:
                            self._received_facebook_data = True

                            self._update_status(
                                PlatformStatusType.CONNECTED,
                                "Facebook chat receiving data",
                            )

                            logger.info(
                                "Facebook chat data received "
                                "through Social Stream Ninja."
                            )

                        # --------------------------------------------------
                        # Message ID / duplicate detection
                        #
                        # IMPORTANT:
                        # We only MARK the message as seen AFTER
                        # it successfully enters the queue.
                        #
                        # This prevents a QueueFull event from
                        # permanently losing the message.
                        # --------------------------------------------------

                        message_id_raw = (
                            message_data.get("id")
                            or message_data.get("message_id")
                        )

                        if message_id_raw is not None:
                            message_id = str(
                                message_id_raw
                            )

                            if (
                                message_id
                                in self.seen_message_ids
                            ):
                                continue
                        else:
                            message_id = None

                        # --------------------------------------------------
                        # User information
                        # --------------------------------------------------

                        user_id = str(
                            message_data.get("userid")
                            or message_data.get("user_id")
                            or message_data.get("username")
                            or message_data.get("chatname")
                            or "unknown"
                        )

                        username = str(
                            message_data.get("username")
                            or message_data.get("chatname")
                            or user_id
                        )

                        # --------------------------------------------------
                        # Local receive timestamp
                        # --------------------------------------------------

                        received_at = time.monotonic()

                        # --------------------------------------------------
                        # Build shared chat message
                        # --------------------------------------------------

                        message = ChatMessage(
                            platform=(
                                Platform.FACEBOOK.value
                            ),
                            user_id=user_id,
                            username=username,
                            text=text,
                            received_at=received_at,
                            message_id=message_id,
                        )

                        # --------------------------------------------------
                        # Put message into shared queue
                        # --------------------------------------------------

                        try:
                            self.chat_queue.put_nowait(
                                message
                            )

                            # Mark as seen ONLY after queue insertion
                            # succeeds.
                            if message_id is not None:
                                self._mark_seen(
                                    message_id
                                )

                        except asyncio.QueueFull:
                            self.dropped_messages += 1

                            logger.warning(
                                "Facebook chat queue full; "
                                "dropped message from %s "
                                "(total dropped: %d)",
                                username,
                                self.dropped_messages,
                            )

            # --------------------------------------------------------------
            # Normal cancellation
            # --------------------------------------------------------------

            except asyncio.CancelledError:
                raise

            # --------------------------------------------------------------
            # Connection failure
            # --------------------------------------------------------------

            except Exception as exc:
                if not self._running:
                    break

                self.status.reconnect_attempts += 1

                self._update_status(
                    PlatformStatusType.DISCONNECTED,
                    f"Social Stream Ninja error: {exc}",
                )

                logger.exception(
                    "Facebook Social Stream Ninja connection failed."
                )

            if self._running:
                # Wait before reconnecting.
                await asyncio.sleep(5)

        # Listener has stopped.
        self._update_status(
            PlatformStatusType.DISCONNECTED,
            "Facebook chat listener stopped",
        )

    async def stop(self) -> None:
        """Stop the Facebook chat listener cleanly."""
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