"""Matcher Unit Tests."""
from app.core.matcher import match_answer


def test_text_fuzzy_and_exact():
    assert match_answer("Paris", ["Paris"])
    assert match_answer("paris!!!", ["Paris"])
    assert match_answer("Pariss", ["Paris"])  # rapidfuzz score >= 85
    assert not match_answer("London", ["Paris"])


def test_numeric_equivalence():
    assert match_answer("1,000,000", ["1000000"])
    assert match_answer("1 000 000", ["1000000"])
    assert match_answer("3.14", ["3,14"])
    assert not match_answer("1000001", ["1000000"])


def test_numbered_text_strictness():
    assert match_answer("Apollo 11", ["Apollo 11"])
    assert not match_answer("Apollo 12", ["Apollo 11"])


def test_roman_numerals():
    assert match_answer("VIII", ["VIII"])
    assert not match_answer("VII", ["VIII"])


def test_short_answer_protection():
    assert match_answer("cat", ["cat"])
    assert not match_answer("car", ["cat"])  # Strict match required for length <= 3

def test_text_normalization_edge_cases():
    assert match_answer("  Paris  ", ["Paris"])
    assert match_answer("PARIS", ["Paris"])
    assert match_answer("Paris!!!", ["Paris"])
    assert match_answer("París", ["París"])
    assert not match_answer("", ["Paris"])
    assert not match_answer("   ", ["Paris"])


def test_unicode_normalization():
    assert match_answer("１２３", ["123"])
    assert match_answer("Ｃａｔ", ["Cat"])


def test_fuzzy_matching_does_not_make_short_answers_fuzzy():
    assert not match_answer("ca", ["cat"])
    assert not match_answer("car", ["cat"])


def test_number_formats():
    assert match_answer("1000", ["1,000"])
    assert match_answer("1.000", ["1000"])
    assert match_answer("1,5", ["1.5"])
    assert match_answer("1.50", ["1,5"])
    assert not match_answer("1.51", ["1.5"])


def test_numbered_answers_do_not_use_fuzzy_matching():
    assert match_answer("Apollo 11", ["Apollo 11"])
    assert not match_answer("Apollo 1", ["Apollo 11"])
    assert not match_answer("Apollo 12", ["Apollo 11"])


def test_roman_answers_are_exact():
    assert match_answer("IV", ["IV"])
    assert match_answer("iv", ["IV"])
    assert not match_answer("VI", ["IV"])
    assert not match_answer("IIII", ["IV"])