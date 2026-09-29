"""Which companies a question names, before anything is retrieved (the owner's second review,
2026-09-29): another company must stop the question, never borrow one of ours."""

import pytest

from app.chat.entities import other_companies


@pytest.mark.parametrize(
    ("question", "others"),
    [
        ("What is Infosys's FY2025 revenue?", ("Infosys",)),
        ("What is ICICI Bank's net profit?", ("ICICI Bank",)),
        ("What is Apple revenue?", ("Apple",)),
        ("Compare TCS with Infosys.", ("Infosys",)),
        ("TCS vs Wipro on margins", ("Wipro",)),
        ("Is TCS or Accenture bigger?", ("Accenture",)),
        ("What was the net profit of Zomato in FY2025?", ("Zomato",)),
        ("How did Bajaj Finance's profit grow?", ("Bajaj Finance",)),
        ("what is infosys revenue", ("infosys",)),  # a known name, in any case
        ("What was HDFC Life's net profit?", ("HDFC Life",)),  # longer than one of ours
        ("What is Reliance Power's debt?", ("Reliance Power",)),
        ("Tata Motors share price", ("Tata Motors",)),
        ("What are L&T's earnings?", ("L&T",)),
    ],
)
def test_another_company_is_found(question: str, others: tuple[str, ...]) -> None:
    assert other_companies(question) == others


@pytest.mark.parametrize(
    "question",
    [
        "What was Reliance's revenue in FY2024?",
        "What is Reliance Industries' net profit?",
        "What was Tata Consultancy Services' revenue?",
        "What was HDFC Bank's net profit in FY2025?",
        "Compare TCS, HDFC Bank, and Reliance on revenue.",
        "What was Jio's revenue?",  # a Reliance business
        "What is Reliance Retail's revenue?",
        "What did Mukesh Ambani's letter say?",  # a person, not in a company position
        "What is HDFC Bank's CASA ratio and NIM?",
        "What did the RBI say about HDFC Bank's deposits?",
        "What was TCS's EPS in Q3 FY2026?",
        "What is TCS's AI revenue?",
        "How does TCS compare with the Nifty?",
        "What will TCS's share price be next year?",
        "I'm a conservative investor. Which should I research further?",
        "What is the weather in Mumbai?",  # no company at all
        "What did management say in March 2026 about revenue?",
        "Compare the same metric for TCS across FY2024, FY2025, and FY2026. Tell me more.",
        "Based only on the available data, what are the three biggest risks facing TCS?",
        "Give me only claims you can support. If you cannot, say so.",
    ],
)
def test_our_stocks_their_businesses_and_other_capitalised_words_are_not_other_companies(
    question: str,
) -> None:
    assert other_companies(question) == ()


def test_each_other_company_is_named_once_in_order() -> None:
    assert other_companies("Compare Infosys, Wipro and Infosys revenue with TCS") == (
        "Infosys",
        "Wipro",
    )


# --- the reviewer's probes: never refuse a question about our own stocks --------------------------


@pytest.mark.parametrize(
    "question",
    [
        "What is Reliance's Consolidated net profit?",
        "What is Reliance's Standalone revenue?",
        "What is HDFC Bank's Gross NPA?",
        "What is Reliance's Free cash flow and Net Debt?",
        "What's TCS's Order Book growth?",
        "What is Reliance's Digital Services revenue?",
        "What is Reliance Jio's ARPU growth?",
        "What is HDFC Bank's Q2 revenue and Basel III ratio?",
        "What was Mukesh Ambani's share of profit?",
        "How did Jamnagar refining affect Reliance's margins?",
    ],
)
def test_a_capitalised_word_that_qualifies_our_own_figure_is_not_another_company(
    question: str,
) -> None:
    assert other_companies(question) == ()


@pytest.mark.parametrize(
    ("question", "others"),
    [
        ("What is Nestle India's revenue?", ("Nestle India",)),
        ("How much profit did Hindustan Zinc make?", ("Hindustan Zinc",)),
        ("paytm net profit", ("paytm",)),
        ("What is Zomato's revenue?", ("Zomato",)),
        ("Is TCS or Accenture bigger?", ("Accenture",)),
    ],
)
def test_more_other_companies_are_found(question: str, others: tuple[str, ...]) -> None:
    assert other_companies(question) == others


@pytest.mark.parametrize(
    "question",
    ["What is TCS's Americas revenue?", "What is Reliance's Jio Platforms revenue?"],
)
def test_a_name_right_after_one_of_ours_qualifies_our_figure(question: str) -> None:
    assert other_companies(question) == ()
