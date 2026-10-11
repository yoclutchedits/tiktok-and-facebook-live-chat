"""Answer matcher module implementing strict specification rules."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from typing import Sequence

from rapidfuzz import fuzz

ROMAN_REGEX = re.compile(
    r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$", re.IGNORECASE
)


class AnswerType(Enum):
    TEXT = auto()
    NUMBER = auto()
    NUMBERED_TEXT = auto()
    ROMAN_NUMERAL = auto()
    MCQ = auto()

def normalize_text(text: str) -> str:
    """Unicode normalize, casefold, strip punctuation and whitespace."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    cleaned = re.sub(r"[^\w\s]", "", normalized)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_canonical_decimal(text: str) -> Decimal | None:
    """Parse string into a finite Decimal handling European/US separator styles.

    Separator rules (applied symmetrically to viewer input and expected answers):
    - "1,234.5" / "1.234,5": the last separator is the decimal point.
    - "1,5" -> 1.5 (one comma followed by 1-2 digits); "1,000" -> 1000.
    - "1.000" -> 1000 (one dot followed by exactly 3 digits after a 1-3 digit,
      non-zero integer part is a European thousands separator);
      "3.14", "0.500" and "1.5" stay decimals; "1.234.567" -> 1234567.
    NaN / Infinity are never numbers (they are valid words such as "Nan").
    """
    cleaned = text.strip().replace(" ", "")
    if not cleaned:
        return None

    has_dot = "." in cleaned
    has_comma = "," in cleaned

    if has_dot and has_comma:
        if cleaned.rfind(".") > cleaned.rfind(","):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif has_comma:
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif has_dot:
        parts = cleaned.split(".")
        if len(parts) > 2:
            cleaned = cleaned.replace(".", "")
        else:
            integer_part = parts[0].lstrip("+-")
            if (
                len(parts[1]) == 3
                and parts[1].isdigit()
                and integer_part.isdigit()
                and 1 <= len(integer_part) <= 3
                and integer_part.strip("0") != ""
            ):
                cleaned = cleaned.replace(".", "")

    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None

    return value if value.is_finite() else None


def classify_answer(raw_answer: str) -> AnswerType:
    """Classify input prior to destructive normalization."""
    cleaned = raw_answer.strip()
    if parse_canonical_decimal(cleaned) is not None:
        return AnswerType.NUMBER

    tokens = cleaned.split()
    if len(tokens) == 1 and ROMAN_REGEX.match(tokens[0]) and len(tokens[0]) > 0:
        return AnswerType.ROMAN_NUMERAL

    if any(char.isdigit() for char in cleaned):
        return AnswerType.NUMBERED_TEXT

    return AnswerType.TEXT


FUZZY_THRESHOLD = 90.0


def _resolve_type(expected: str, explicit_type: str | None) -> AnswerType:
    if explicit_type:
        try:
            return AnswerType[explicit_type.upper()]
        except KeyError:
            try:
                return AnswerType(explicit_type.lower())
            except ValueError:
                pass
    return classify_answer(expected)


def _strip_punct(value: str) -> str:
    return re.sub(r"[^\w\s]", "", value)


def matches_answer(
    user_input: str,
    expected_answers: Sequence[str],
    fuzzy_threshold: float = FUZZY_THRESHOLD,
    explicit_type: str | None = None,
) -> bool:
    """Match user input against valid answers following spec precedence.

    The answer type is resolved FIRST. Punctuation-stripped text equality is
    only a valid shortcut for text-like answers; applying it to numbers made
    "-5" equal "5" and "3.14" equal "314".
    """
    norm_user = normalize_text(user_input)
    if not norm_user:
        return False

    for expected in expected_answers:
        norm_expected = normalize_text(expected)
        if not norm_expected:
            continue

        ans_type = _resolve_type(expected, explicit_type)

        # 1. Numbers: compare canonical decimals only (keeps sign and point).
        if ans_type == AnswerType.NUMBER:
            user_dec = parse_canonical_decimal(user_input)
            exp_dec = parse_canonical_decimal(expected)
            if user_dec is not None and exp_dec is not None and user_dec == exp_dec:
                return True
            continue

        # 2. MCQ answers are strict exact matches (case-insensitive).
        if ans_type == AnswerType.MCQ:
            if _strip_punct(norm_user).upper() == _strip_punct(norm_expected).upper():
                return True
            continue

        # 3. Numbered text & Roman numerals: strict exact match, NO fuzzy.
        if ans_type in (AnswerType.NUMBERED_TEXT, AnswerType.ROMAN_NUMERAL):
            if _strip_punct(norm_user) == _strip_punct(norm_expected):
                return True
            continue

        # 4. Text: exact normalized match.
        if norm_user == norm_expected:
            return True

        # 5. Short answer protection (1-3 chars must match exactly).
        if len(norm_expected) <= 3:
            continue

        # 6. Fuzzy text matching (rapidfuzz.fuzz.ratio ONLY).
        if fuzz.ratio(norm_user, norm_expected) >= fuzzy_threshold:
            return True

    return False


# Backward-compatible alias for existing tests
match_answer = matches_answer