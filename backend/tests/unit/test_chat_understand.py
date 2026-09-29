"""What a chat question is about, read by code (P12): stocks, metrics, growth, events, periods."""

import pytest

from app.chat.contract import Turn
from app.chat.understand import ALL_SYMBOLS, Question, understand


def ask(question: str, history: list[Turn] | None = None) -> Question:
    return understand(question, history=history or [])


# --- stocks ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "symbol"),
    [
        ("How is Reliance doing?", "RELIANCE"),
        ("What about RIL?", "RELIANCE"),
        ("reliance industries profit", "RELIANCE"),
        ("TCS revenue", "TCS"),
        ("tata consultancy services revenue", "TCS"),
        ("Tata Consultancy dividend", "TCS"),
        ("HDFC Bank NIM", "HDFCBANK"),
        ("hdfcbank npa", "HDFCBANK"),
        ("How is HDFC doing?", "HDFCBANK"),
    ],
)
def test_each_stock_is_found_by_its_names_in_any_case(text: str, symbol: str) -> None:
    assert ask(text).symbols == (symbol,)


def test_names_count_only_as_whole_words() -> None:
    # "tcsx" and "hdfclife" are other words; with no stock named, all three are meant
    assert ask("Is tcsx like hdfclife?").symbols == ALL_SYMBOLS


def test_stocks_come_in_order_of_mention_and_only_once() -> None:
    question = ask("Compare TCS with Reliance, then TCS with HDFC Bank")
    assert question.symbols == ("TCS", "RELIANCE", "HDFCBANK")
    assert question.from_history is False


def test_a_follow_up_takes_the_stocks_of_the_latest_user_turn_that_named_some() -> None:
    history = [
        Turn(role="user", text="How much debt does TCS have?"),
        Turn(role="assistant", text="TCS ... [1]"),
        Turn(role="user", text="And Reliance?"),
        Turn(role="assistant", text="HDFC Bank is not relevant here."),  # the assistant's words
        Turn(role="user", text="thanks"),  # names none: skipped
    ]
    question = ask("and last year?", history)
    assert question.symbols == ("RELIANCE",)
    assert question.from_history is True


def test_with_no_stock_anywhere_the_question_is_about_all_three() -> None:
    history = [Turn(role="assistant", text="Ask me about TCS.")]
    question = ask("Which has the lowest debt?", history)
    assert question.symbols == ("RELIANCE", "TCS", "HDFCBANK")
    assert question.from_history is False


def test_a_stock_in_the_question_wins_over_the_history() -> None:
    history = [Turn(role="user", text="TCS profit?")]
    question = ask("HDFC Bank profit?", history)
    assert (question.symbols, question.from_history) == (("HDFCBANK",), False)


# --- metrics --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "metrics"),
    [
        ("revenue", ("revenue_from_operations", "net_interest_income")),
        ("sales", ("revenue_from_operations", "net_interest_income")),
        ("top line", ("revenue_from_operations", "net_interest_income")),
        ("turnover", ("revenue_from_operations", "net_interest_income")),
        ("profit", ("net_profit",)),
        ("PAT", ("net_profit",)),
        ("earnings", ("net_profit",)),
        ("net income", ("net_profit",)),
        ("debt", ("total_borrowings",)),
        ("borrowings", ("total_borrowings",)),
        ("leverage", ("total_borrowings",)),
        ("equity", ("total_equity",)),
        ("net worth", ("total_equity",)),
        ("dividends", ("dividend_per_share",)),
        ("payout", ("dividend_per_share",)),
        ("EPS", ("eps_basic",)),
        ("earnings per share", ("eps_basic",)),
        ("ROE", ("return_on_equity",)),
        ("return on equity", ("return_on_equity",)),
        ("NIM", ("net_interest_margin",)),
        ("net interest margin", ("net_interest_margin",)),
        ("margin", ("net_interest_margin",)),
        ("net interest income", ("net_interest_income",)),
        ("NPA", ("gross_npa_ratio",)),
        ("bad loans", ("gross_npa_ratio",)),
        ("asset quality", ("gross_npa_ratio",)),
    ],
)
def test_metric_words_map_to_vocabulary_metrics(text: str, metrics: tuple[str, ...]) -> None:
    assert ask(f"TCS {text}?").metrics == metrics


def test_a_longer_phrase_is_not_also_read_as_its_parts() -> None:
    assert ask("TCS return on equity").metrics == ("return_on_equity",)
    assert ask("TCS earnings per share").metrics == ("eps_basic",)


def test_debt_to_equity_asks_for_both_figures_in_vocabulary_order() -> None:
    assert ask("equity and debt of TCS").metrics == ("total_borrowings", "total_equity")


def test_metric_words_count_only_as_whole_words() -> None:
    assert ask("Is TCS patient and profitable?").metrics == ()


def test_a_general_question_names_no_metric() -> None:
    assert ask("Tell me about TCS").metrics == ()


# --- growth and events ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Did TCS grow?",
        "TCS growth",
        "How did profit change?",
        "Did revenue increase",
        "a decrease in debt",
        "did it rise",
        "did it fall",
        "FY2025 vs FY2026",
        "compared with last year",
    ],
)
def test_growth_words_ask_for_growth(text: str) -> None:
    assert ask(text).wants_growth is True


def test_a_plain_question_does_not_ask_for_growth_or_events() -> None:
    question = ask("What is TCS net profit?")
    assert (question.wants_growth, question.wants_events) == (False, False)


@pytest.mark.parametrize(
    "text",
    ["Any news on TCS?", "recent events", "What is the sentiment?", "latest announcements"],
)
def test_news_words_ask_for_events(text: str) -> None:
    assert ask(text).wants_events is True


# --- periods --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "periods"),
    [
        ("TCS profit in FY26", ("FY2026",)),
        ("TCS profit in FY2026", ("FY2026",)),
        ("TCS profit in FY 2025-26", ("FY2026",)),
        ("TCS profit in FY'26", ("FY2026",)),
        ("TCS profit in 2025-26", ("FY2026",)),
        ("TCS profit in Q3 FY26", ("Q3FY2026",)),
        ("TCS profit in Q3FY2026", ("Q3FY2026",)),
        ("TCS profit in 3QFY26", ("Q3FY2026",)),
        ("TCS profit, FY2025 vs FY2026 and FY26 again", ("FY2025", "FY2026")),
    ],
)
def test_periods_are_read_as_fiscal_year_and_quarter_codes(
    text: str, periods: tuple[str, ...]
) -> None:
    assert ask(text).periods == periods


@pytest.mark.parametrize(
    "text",
    [
        "TCS profit in H1 FY26",  # half a year is none of our periods, not the full year
        "TCS profit in 9MFY26",
        "TCS profit in FY 2025-27",  # not one year apart
        "TCS profit",
    ],
)
def test_text_that_is_not_a_full_year_or_quarter_names_no_period(text: str) -> None:
    assert ask(text).periods == ()


# --- "Match me" (P14) -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Match me",
        "match me please",
        "Which stock suits me best?",
        "Which one fits my profile?",
        "What matches my preferences?",
        "Which of them is right for me",
        "Which one aligns best with a conservative, long-term investor?",
        "Is TCS a good fit for me?",
    ],
)
def test_match_questions_ask_for_a_match(text: str) -> None:
    assert ask(text).wants_match is True


@pytest.mark.parametrize(
    "text",
    ["What was TCS's net profit in FY2026?", "Does the revenue match the guidance?", "Suits"],
)
def test_other_questions_do_not_ask_for_a_match(text: str) -> None:
    assert ask(text).wants_match is False
