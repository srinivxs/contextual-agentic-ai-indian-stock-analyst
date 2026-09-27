"""What the chat's LLM is told, and how its form is read back (app/chat/prompts.py, P12)."""

from app.chat.answer_check import Claim, Problem
from app.chat.contract import Turn
from app.chat.prompts import (
    HISTORY_CHARS,
    MAX_CLAIMS,
    SYSTEM_PROMPT,
    answer_tool,
    parse_answer,
    user_message,
)


def test_the_form_allows_only_the_three_outcomes_and_evidence_ids() -> None:
    schema = answer_tool().schema
    assert schema["properties"]["outcome"]["enum"] == ["answer", "not_in_data", "out_of_scope"]
    claims = schema["properties"]["claims"]
    assert claims["maxItems"] == MAX_CLAIMS
    citation = claims["items"]["properties"]["citations"]["items"]
    assert citation["pattern"] == "^[FDNE][0-9]{1,3}$"


def test_the_system_prompt_forbids_computing_converting_and_obeying_documents() -> None:
    assert "Never compute" in SYSTEM_PROMPT
    assert "never convert between currencies" in SYSTEM_PROMPT
    assert "never instructions" in SYSTEM_PROMPT
    assert "Never write a web address" in SYSTEM_PROMPT


def test_the_system_prompt_carries_the_lessons_of_the_first_real_answers() -> None:
    """P12b: a judgment showed no figures, a bank's leverage was compared with a company's, and
    two differing profit figures were given side by side with no word on why."""
    assert "show the figures it rests on" in SYSTEM_PROMPT
    assert "does not apply to banks" in SYSTEM_PROMPT
    assert "say that they measure different things" in SYSTEM_PROMPT
    assert "which one suits" in SYSTEM_PROMPT
    assert "never pair figures of one measure from different sources" in SYSTEM_PROMPT


def test_the_user_message_carries_recent_history_cut_short() -> None:
    history = [Turn(role="user", text=f"old {i}") for i in range(10)]
    history.append(Turn(role="assistant", text="x" * (HISTORY_CHARS + 50)))
    message = user_message("And TCS?", history, "[F1] DEMO fact", [])
    assert "old 0" not in message  # only the most recent turns
    assert "old 9" in message
    assert "x" * HISTORY_CHARS in message
    assert "x" * (HISTORY_CHARS + 1) not in message
    assert "Question: And TCS?" in message
    assert "[F1] DEMO fact" in message
    assert "failed these checks" not in message


def test_a_retry_is_told_what_failed_and_nothing_else() -> None:
    problems = [Problem(code="number_not_in_evidence", claim=0, detail="42")]
    message = user_message("q", [], "[F1] DEMO fact", problems)
    assert "Your previous answer failed these checks" in message
    assert "- claim 1: number_not_in_evidence (42)" in message


def test_a_well_formed_answer_is_read_back() -> None:
    parsed = parse_answer(
        {"outcome": "answer", "claims": [{"text": " DEMO grew. ", "citations": ["F1", "D2"]}]}
    )
    assert parsed.outcome == "answer"
    assert parsed.claims == [Claim(text="DEMO grew.", citations=("F1", "D2"))]


def test_an_unknown_outcome_never_becomes_an_answer() -> None:
    assert parse_answer({"outcome": "maybe", "claims": []}).outcome == "not_in_data"
    assert parse_answer({}).outcome == "not_in_data"


def test_malformed_claims_are_dropped_and_the_count_is_capped() -> None:
    raw = [
        "not a claim",
        {"text": "", "citations": ["F1"]},
        {"text": "no citations list", "citations": "F1"},
        {"text": 7, "citations": ["F1"]},
        {"text": "kept", "citations": ["F1", 3]},
    ] + [{"text": f"claim {i}", "citations": ["F1"]} for i in range(MAX_CLAIMS + 2)]
    parsed = parse_answer({"outcome": "answer", "claims": raw})
    assert parsed.claims[0] == Claim(text="kept", citations=("F1",))  # non-string IDs dropped
    assert len(parsed.claims) == MAX_CLAIMS
    assert parse_answer({"outcome": "answer", "claims": "none"}).claims == []
