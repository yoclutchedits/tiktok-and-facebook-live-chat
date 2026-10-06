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
    """

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
        if message_id in self.seen_message_ids:
            return False

        if len(self._seen_order) >= self._seen_order.maxlen:
            oldest = self._seen_order.popleft()
            self.seen_message_ids.discard(oldest)

        self._seen_order.append(message_id)
        self.seen_message_ids.add(message_id)

        return True

    @staticmethod
    def _unwrap_message(data: Any) -> dict[str, Any] | None:
        """
        Social Stream Ninja payloads can be wrapped.

        Try the common wrapper shapes and return the actual
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

        for key in ("data", "message", "value", "payload"):
            nested = data.get(key)

            if isinstance(nested, dict):
                result = FacebookAdapter._unwrap_message(nested)

                if result is not None:
                    return result

            elif isinstance(nested, str):
                try:
                    decoded = json.loads(nested)
                except (TypeError, ValueError):
                    continue

                result = FacebookAdapter._unwrap_message(decoded)

                if result is not None:
                    return result

        return None

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        """
        Prefer plain text metadata when Social Stream Ninja provides it.

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
        uri = (
            "wss://io.socialstream.ninja/join/"
            f"{self.session_id}/4"
        )

        while self._running:
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

                    self._update_status(
                        PlatformStatusType.CONNECTED,
                        "Facebook chat connected via Social Stream Ninja",
                    )

                    logger.info(
                        "Facebook chat connected through "
                        "Social Stream Ninja session %s.",
                        self.session_id,
                    )

                    async for raw_message in websocket:
                        if not self._running:
                            break

                        try:
                            data = json.loads(raw_message)
                        except (TypeError, ValueError):
                            logger.warning(
                                "Ignoring invalid Social Stream "
                                "JSON message."
                            )
                            continue

                        message_data = self._unwrap_message(data)

                        if message_data is None:
                            continue

                        # Only accept Facebook chat.
                        source_type = str(
                            message_data.get("type")
                            or message_data.get("platform")
                            or ""
                        ).strip().lower()

                        if source_type and source_type != "facebook":
                            continue

                        text = self._extract_text(message_data)

                        if not text:
                            continue

                        message_id_raw = (
                            message_data.get("id")
                            or message_data.get("message_id")
                        )

                        if message_id_raw is not None:
                            message_id = str(message_id_raw)

                            if not self._mark_seen(message_id):
                                continue
                        else:
                            message_id = None

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

                        received_at = time.monotonic()

                        message = ChatMessage(
                            platform=Platform.FACEBOOK.value,
                            user_id=user_id,
                            username=username,
                            text=text,
                            received_at=received_at,
                            message_id=message_id,
                        )

                        try:
                            self.chat_queue.put_nowait(
                                message
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

            except asyncio.CancelledError:
                raise

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

                await asyncio.sleep(3)

        self._update_status(
            PlatformStatusType.DISCONNECTED,
            "Facebook chat listener stopped",
        )

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