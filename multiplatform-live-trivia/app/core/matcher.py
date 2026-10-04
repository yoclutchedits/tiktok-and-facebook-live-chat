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
    """Parse string into Decimal handling European/US separator styles."""
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

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


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


def matches_answer(
    user_input: str,
    expected_answers: Sequence[str],
    fuzzy_threshold: float = 85.0,
    explicit_type: str | None = None,
) -> bool:
    """Match user input against valid answers following spec precedence."""
    norm_user = normalize_text(user_input)
    if not norm_user:
        return False

    for expected in expected_answers:
        norm_expected = normalize_text(expected)
        if not norm_expected:
            continue

        # Exact normalized match check
        if norm_user == norm_expected:
            return True

        # Determine type
        if explicit_type:
            try:
                ans_type = AnswerType[explicit_type.upper()]
            except KeyError:
                try:
                    ans_type = AnswerType(explicit_type.lower())
                except ValueError:
                    ans_type = classify_answer(expected)
        else:
            ans_type = classify_answer(expected)

        # 1. Canonical Decimal Comparison
        if ans_type == AnswerType.NUMBER:
            user_dec = parse_canonical_decimal(user_input)
            exp_dec = parse_canonical_decimal(expected)
            if user_dec is not None and exp_dec is not None and user_dec == exp_dec:
                return True
            continue
        # 2. MCQ answers are strict exact matches.
        if ans_type == AnswerType.MCQ:
            strip_user = re.sub(r"[^\w\s]", "", norm_user)
            strip_exp = re.sub(r"[^\w\s]", "", norm_expected)

            if strip_user.upper() == strip_exp.upper():
                return True

            continue

        # 3. Numbered Text & Roman Numerals (Strict exact match, NO fuzzy)
        if ans_type in (AnswerType.NUMBERED_TEXT, AnswerType.ROMAN_NUMERAL):
            strip_user = re.sub(r"[^\w\s]", "", norm_user)
            strip_exp = re.sub(r"[^\w\s]", "", norm_expected)
            if strip_user == strip_exp:
                return True
            continue

        # 4. Short Answer Protection (1-3 chars must match exactly)
        if len(norm_expected) <= 3:
            continue

        # 5. Fuzzy Text Matching (rapidfuzz.fuzz.ratio ONLY)
        similarity = fuzz.ratio(norm_user, norm_expected)
        if similarity >= fuzzy_threshold:
            return True

    return False


# Backward-compatible alias for existing tests
match_answer = matches_answer