"""The investor profile's fixed vocabulary and shapes (P13; the project notes "Memory").

Every part of P13 agrees on this module: the extractor (app/memory/extract.py) only produces these
values, the table only stores them (migration 0009), the api only accepts them, and the panel only
offers them. A fixed vocabulary is what lets code, not a model, read preferences, and what lets
P14's matching turn them into rules.

    field               how many   values
    risk_preference     one        conservative, moderate, aggressive
    debt_preference     one        avoid_high_debt, debt_ok
    investment_style    1 to 5     income, growth, quality, value, momentum
    other_preferences   1 to 3     long_term, short_term, stability

Merge rule: a newer statement about a field replaces that field's values entirely ("I'm
aggressive" after "I'm conservative" leaves aggressive); fields the statement does not mention
are kept. Value and momentum can be remembered but not matched (no prices): P14 says so.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Field = Literal["risk_preference", "debt_preference", "investment_style", "other_preferences"]
FIELDS: tuple[Field, ...] = (
    "risk_preference",
    "debt_preference",
    "investment_style",
    "other_preferences",
)
SINGLE_VALUED: frozenset[Field] = frozenset({"risk_preference", "debt_preference"})

FIELD_LABELS: dict[Field, str] = {
    "risk_preference": "Risk",
    "debt_preference": "Debt",
    "investment_style": "Style",
    "other_preferences": "Other",
}

# value -> label, in the order the panel offers them
CHOICES: dict[Field, dict[str, str]] = {
    "risk_preference": {
        "conservative": "Conservative",
        "moderate": "Moderate",
        "aggressive": "Aggressive",
    },
    "debt_preference": {
        "avoid_high_debt": "Avoid high debt",
        "debt_ok": "Debt is fine",
    },
    "investment_style": {
        "income": "Dividends / income",
        "growth": "Growth",
        "quality": "Quality",
        "value": "Value",
        "momentum": "Momentum",
    },
    "other_preferences": {
        "long_term": "Long-term horizon",
        "short_term": "Short-term horizon",
        "stability": "Stable, steady results",
    },
}

MAX_QUOTE_CHARS = 300  # the supporting quote: the user's own sentence, cut to this


@dataclass(frozen=True)
class Preference:
    """What one user message says about one field: its values and the sentence that says so."""

    field: Field
    values: tuple[str, ...]  # from CHOICES[field]; exactly one for a SINGLE_VALUED field
    quote: str  # the user's own sentence, verbatim (cut to MAX_QUOTE_CHARS)


@dataclass(frozen=True)
class StoredPreference:
    """One remembered field as the store returns it."""

    field: Field
    values: tuple[str, ...]
    quote: str  # the user's words; for a field set in the panel, the chosen labels
    source: Literal["chat", "edited"]
    updated_at: datetime


def valid_values(field: Field, values: tuple[str, ...]) -> bool:
    """True if the values are allowed for the field: known, distinct, and the right count."""
    if not values or len(set(values)) != len(values):
        return False
    if field in SINGLE_VALUED and len(values) != 1:
        return False
    return all(value in CHOICES[field] for value in values)


def labels(field: Field, values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(CHOICES[field][value] for value in values)
