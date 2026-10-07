"""Which stock an RBI press release names, and how it is tagged (P15). Deterministic, no LLM.

Both jobs are done by plain rules the owner can read (no LLM where code will do): a name table
for "who", and a short pattern table for "what kind of event, good or bad, how big". Anything
the rules do not recognise is tagged the most neutral way ('other', 'neutral', 'low'), never
guessed at.
"""

import re
from collections.abc import Mapping

# What each followed stock is called in a title. Whole words only; case does not matter.
ALIASES: dict[str, tuple[str, ...]] = {
    "RELIANCE": ("Reliance Industries",),
    "TCS": ("Tata Consultancy Services", "TCS"),
    "HDFCBANK": ("HDFC Bank",),
}

Tag = tuple[str, str, str]  # (event_type, sentiment, impact), all from app/vocabulary.py

# The first row that matches wins, so the strongest signals come first.
_PATTERNS: tuple[tuple[str, Tag], ...] = (
    (
        r"cancel(?:s|led|lation)?\b.{0,40}\b(?:licen[cs]e|registration)"
        r"|\bsection 35a\b",
        ("regulatory_legal", "negative", "high"),
    ),
    (r"\bpenalty\b|\bpenalties\b", ("regulatory_legal", "negative", "medium")),
    (r"\b(?:re-?)?appointment\b", ("management_change", "neutral", "low")),
    (r"\bapproves?\b|\bapproval\b|\bapproved\b", ("regulatory_legal", "neutral", "low")),
)
_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), result) for pattern, result in _PATTERNS)
_NOTHING_SPECIAL: Tag = ("other", "neutral", "low")


def _whole_word(alias: str) -> re.Pattern[str]:
    # Not \b: an alias may end in punctuation ("Co."), where \b would fail before a space.
    return re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)


def stocks_named(text: str, aliases: Mapping[str, tuple[str, ...]] = ALIASES) -> tuple[str, ...]:
    """The symbols whose names appear in ``text``, in the table's order, once each."""
    return tuple(
        symbol
        for symbol, names in aliases.items()
        if any(_whole_word(name).search(text) for name in names)
    )


def tag(title: str) -> Tag:
    """(event type, sentiment, impact) for a press-release title."""
    for pattern, result in _COMPILED:
        if pattern.search(title):
            return result
    return _NOTHING_SPECIAL
