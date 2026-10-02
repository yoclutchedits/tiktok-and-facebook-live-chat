
"""Question loader service.

Keep this module as a compatibility layer over the core loader so
all entry points share the same validation rules.
"""

from app.core.question_loader import (
    QuestionLoader,
    load_questions,
    validate_and_parse_questions,
)

__all__ = [
    "QuestionLoader",
    "load_questions",
    "validate_and_parse_questions",
]