"""Chat-title normalisation (the LLM call itself is exercised live, not here)."""

from assistant.title import _clean_title


def test_clean_strips_quotes_and_trailing_punctuation():
    assert _clean_title('"Trip to Sydney."') == "Trip to Sydney"
    assert _clean_title("Budget Planning!") == "Budget Planning"


def test_clean_collapses_whitespace_and_newlines():
    assert _clean_title("  Heater   Repair\nEmail  ") == "Heater Repair Email"


def test_clean_caps_length():
    assert len(_clean_title("word " * 50)) <= 80


def test_clean_empty_is_none():
    assert _clean_title("") is None
    assert _clean_title(None) is None
    assert _clean_title('  "" ') is None
