"""Read investor preferences out of one chat message, by deterministic code (P13; no LLM).

Preventing memory poisoning -- text that should never set the user's profile -- comes from a
short, fixed set of rules, applied per sentence:

    a. Split the message into sentences on ``. ! ? ;`` and line breaks, each sentence keeping its
       own end mark (so a caller can tell a question from a statement).
    b. Skip questions: a sentence ending in "?" never writes memory ("Should I avoid high debt?").
    c. Remove quoted spans first (``"..."``, curly ``"..."``, and ``'...'`` of 3+ words opening
       and closing away from letters, so contractions are not quotes) so pasted text a user
       quotes ("I read: \"we are aggressive\"") does not count. Curly apostrophes read as
       straight ones.
    d. First person singular only: a sentence counts only if it contains I, I'm, I am, Im, my, me,
       I've, I'd, I'll or myself (whole words, any case). "We", "our" and "the company" never
       count, so text copied from a filing cannot set memory.
    e. Cue phrases per value (the table below) decide which value a sentence names. Longer,
       more specific phrases are looked for first and each matched span is consumed once, so a
       shorter phrase inside it is never read again.
    f. Negation: a cue is ignored if one of "not, no, never, don't, do not, doesn't, isn't, n't,
       without" (or dont, doesnt, isnt, wont, cant, cannot, typed without an apostrophe) is among
       the three words right before it ("I'm not conservative", "I don't want
       dividends") -- unless the cue phrase itself already contains the negation (debt_ok's
       "don't mind debt"). "avoid" is not a negation.
    g. Single-valued fields (see ``SINGLE_VALUED``): if one sentence names two values, the last
       one mentioned wins; if several sentences mention the same field, the last sentence wins
       (its values, its quote). Multi-valued fields keep every value found in that last sentence,
       in the vocabulary's order.
    h. Nothing found means an empty list.

Cue phrases, in the order the table lists them (whole words, any case):

    risk conservative   conservative, cautious, risk-averse / risk averse, low risk / low-risk,
                         safe, safety
    risk moderate        moderate, balanced, medium risk
    risk aggressive      aggressive, high risk / high-risk, risk taker / risk-taker
    debt avoid_high_debt avoid (high|too much)? debt, avoid(s|ing)? (highly )?leveraged/leverage,
                         low debt, little debt, debt-free / debt free, low leverage
    debt debt_ok         (don't|do not) mind debt, debt is (fine|ok|okay),
                         comfortable with (debt|leverage), fine with (debt|leverage)
    style income         dividends?, dividend-focused, income
    style growth          growth, growing companies
    style quality         quality, strong balance sheets?
    style value           value investor/investing/stocks, undervalued (never the bare word
                         "value": "I value dividends" sets income, not value)
    style momentum        momentum
    other long_term       long-term / long term, for years, a decade
    other short_term      short-term / short term, trading, quick gains
    other stability       stable, steady, consistent, predictable
"""

import re

from app.memory.vocabulary import (
    CHOICES,
    FIELD_LABELS,
    FIELDS,
    MAX_QUOTE_CHARS,
    SINGLE_VALUED,
    Field,
    Preference,
    StoredPreference,
    labels,
)

# Sentences end on one of these, the mark kept so a question can be told from a statement.
_SENTENCE = re.compile(r"[^.!?;\n]+[.!?;\n]*")

# Quoted spans removed before anything else is read: double/curly quotes of any length, single
# quotes only when they hold 3 or more words and open and close away from letters (so the
# apostrophes of "I'm aggressive and I'd like growth" are never read as a quotation).
_DOUBLE_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
_SINGLE_QUOTED_LONG = re.compile(r"(?<![A-Za-z])'(?:[^'\s]+\s+){2,}[^'\s]+'(?![A-Za-z])")

_FIRST_PERSON = re.compile(r"\b(?:i|i'm|im|i am|my|me|i've|i'd|i'll|myself)\b", re.IGNORECASE)

# One of the previous 3 words blocks the cue that follows it, unless the cue phrase itself already
# contains the negation (it is then part of the match, not of the words before it).
_NEGATION = re.compile(
    r"\b(?:not|no|never|without|dont|doesnt|isnt|wont|cant|cannot)\b|n't\b|\bdo not\b",
    re.IGNORECASE,
)

# value -> the phrases that name it, as regex fragments (joined with "|" and word-bounded below).
_CUES: dict[Field, dict[str, tuple[str, ...]]] = {
    "risk_preference": {
        "conservative": (
            "conservative",
            "cautious",
            "risk[- ]averse",
            "low[- ]risk",
            "safe",
            "safety",
        ),
        "moderate": ("moderate", "balanced", "medium risk"),
        "aggressive": ("aggressive", "high[- ]risk", "risk[- ]taker"),
    },
    "debt_preference": {
        "avoid_high_debt": (
            r"avoid(?:s|ing)?\s+(?:high\s+|too much\s+)?debt",
            r"avoid(?:s|ing)?\s+(?:highly\s+)?leveraged",
            r"avoid(?:s|ing)?\s+leverage",
            r"low\s+debt",
            r"little\s+debt",
            "debt[- ]free",
            r"low\s+leverage",
        ),
        "debt_ok": (
            r"(?:don'?t|do not)\s+mind\s+debt",
            r"debt\s+is\s+(?:fine|ok|okay)",
            r"comfortable\s+with\s+(?:debt|leverage)",
            r"fine\s+with\s+(?:debt|leverage)",
        ),
    },
    "investment_style": {
        "income": ("dividends?", "dividend-focused", "income"),
        "growth": ("growth", "growing companies"),
        "quality": ("quality", "strong balance sheets?"),
        "value": (r"value\s+(?:investor|investing|stocks)", "undervalued"),
        "momentum": ("momentum",),
    },
    "other_preferences": {
        "long_term": ("long[- ]term", "for years", "a decade"),
        "short_term": ("short[- ]term", "trading", "quick gains"),
        "stability": ("stable", "steady", "consistent", "predictable"),
    },
}


def _compile_cues() -> tuple[tuple[Field, str, re.Pattern[str]], ...]:
    compiled = []
    for field in FIELDS:
        for value in CHOICES[field]:
            pattern = re.compile(rf"\b(?:{'|'.join(_CUES[field][value])})\b", re.IGNORECASE)
            compiled.append((field, value, pattern))
    return tuple(compiled)


_CUE_PATTERNS = _compile_cues()


def _sentences(message: str) -> list[str]:
    """The message split into sentences on ``. ! ? ;`` and line breaks (rule a)."""
    return [s.strip() for s in _SENTENCE.findall(message) if s.strip()]


def _remove_quotes(sentence: str) -> str:
    """The sentence with quoted spans blanked out, for reading cues in (rule c)."""
    sentence = _DOUBLE_QUOTED.sub(" ", sentence)
    return _SINGLE_QUOTED_LONG.sub(" ", sentence)


def _is_negated(text_before: str) -> bool:
    """True if a negation sits among the 3 words right before a cue (rule f)."""
    window = " ".join(text_before.split()[-3:])
    return _NEGATION.search(window) is not None


def _find_cues(text: str) -> dict[Field, list[tuple[int, str]]]:
    """(field -> [(position, value), ...] in text order), longer phrases read first (rule e)."""
    working = text
    found: dict[Field, list[tuple[int, str]]] = {field: [] for field in FIELDS}
    for field, value, pattern in _CUE_PATTERNS:
        for match in pattern.finditer(working):
            if not _is_negated(working[: match.start()]):
                found[field].append((match.start(), value))
        # Blank what this pattern read (same length, so later positions stay correct) so a
        # shorter phrase inside it is never read again.
        working = pattern.sub(lambda m: " " * len(m.group()), working)
    for matches in found.values():
        matches.sort(key=lambda item: item[0])
    return found


def _sentence_values(field: Field, field_matches: list[tuple[int, str]]) -> tuple[str, ...]:
    if field in SINGLE_VALUED:
        return (field_matches[-1][1],)  # last mentioned wins (rule g)
    order = list(CHOICES[field])
    found = {value for _, value in field_matches}
    return tuple(sorted(found, key=order.index))  # vocabulary order


def extract_preferences(message: str) -> list[Preference]:
    """The preferences one chat message states about itself, deterministically, no LLM."""
    by_field: dict[Field, Preference] = {}
    for sentence in _sentences(message):
        if sentence.endswith("?"):
            continue  # rule b
        # Curly apostrophes and quotes, as phones type them, read as straight ones (same length).
        cleaned = _remove_quotes(sentence.replace("\u2019", "'").replace("\u2018", "'"))
        if not _FIRST_PERSON.search(cleaned):
            continue  # rule d
        matches = _find_cues(cleaned)
        for field in FIELDS:
            field_matches = matches[field]
            if not field_matches:
                continue
            values = _sentence_values(field, field_matches)
            by_field[field] = Preference(
                field=field, values=values, quote=sentence[:MAX_QUOTE_CHARS]
            )
    return [by_field[field] for field in FIELDS if field in by_field]


def describe(preferences: list[Preference]) -> str:
    """The confirmation shown to the user right after a message sets memory."""
    if not preferences:
        return ""
    parts = [
        f"{FIELD_LABELS[p.field]}: {', '.join(labels(p.field, p.values))}" for p in preferences
    ]
    return "Noted. I'll remember: " + "; ".join(parts) + "."


def profile_summary(stored: list[StoredPreference]) -> str:
    """One line fingerprinting the whole profile, embedded to bias retrieval toward it."""
    by_field = {p.field: p for p in stored}
    parts = [
        f"{FIELD_LABELS[field]}: {', '.join(labels(field, by_field[field].values))}"
        for field in FIELDS
        if field in by_field
    ]
    if not parts:
        return ""
    return "Investor preferences: " + ". ".join(parts) + "."


def preferences_text(stored: list[StoredPreference]) -> str:
    """The stored profile as lines for the chat model: labels only, never the user's quotes."""
    by_field = {p.field: p for p in stored}
    lines = [
        f"- {FIELD_LABELS[field]}: {', '.join(labels(field, by_field[field].values))}"
        for field in FIELDS
        if field in by_field
    ]
    return "\n".join(lines)
