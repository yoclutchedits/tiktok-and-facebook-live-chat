"""Backward-compatible alias for the canonical trivia engine."""

from app.core.engine import TriviaEngine
from app.models import GameState

__all__ = ["TriviaEngine", "GameState"]