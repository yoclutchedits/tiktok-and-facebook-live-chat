"""Shared data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Platform(str, Enum):
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"


class AnswerType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    NUMBERED_TEXT = "numbered_text"
    ROMAN_NUMERAL = "roman_numeral"


class GameState(str, Enum):
    IDLE = "IDLE"
    WAITING_FOR_START = "WAITING_FOR_START"
    ACTIVE = "ACTIVE"
    DRAINING = "DRAINING"
    RESULT = "RESULT"
    TRANSITION = "TRANSITION"
    FINISHED = "FINISHED"
    STOPPED = "STOPPED"


class PlatformStatusType(str, Enum):
    DISABLED = "DISABLED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    ENDED = "ENDED"


class GameCommandType(str, Enum):
    START = "START"
    STOP = "STOP"


@dataclass(frozen=True)
class ChatMessage:
    platform: str
    user_id: str
    username: str
    text: str
    received_at: float
    message_id: Optional[str] = None
    platform_created_at: Optional[datetime] = None


@dataclass
class Player:
    platform: str
    platform_user_id: str
    display_name: str
    score: int = 0
    score_reached_at: float = 0.0
    score_history: List[float] = field(default_factory=list)

    @property
    def player_key(self) -> str:
        return f"{self.platform}:{self.platform_user_id}"


@dataclass(frozen=True)
class Question:
    question: str
    answers: Tuple[str, ...]
    answer_type: Optional[AnswerType] = None


@dataclass(frozen=True)
class GameCommand:
    command: GameCommandType


@dataclass
class PlatformStatus:
    platform: str
    state: PlatformStatusType = PlatformStatusType.DISABLED
    detail: str = ""
    reconnect_attempts: int = 0


@dataclass
class GameSnapshot:
    state: str
    question_number: int
    total_questions: int
    question: Optional[str]
    deadline: Optional[float]
    platforms: Dict[str, Any]
    queue_size: int
    queue_capacity: int
    dropped_messages: int
    leaderboard: List[Dict[str, Any]]

    time_remaining: Optional[float] = None
    correct_answer: Optional[str] = None