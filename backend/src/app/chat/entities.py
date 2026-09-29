"""Which other companies a question names, found by code before anything is retrieved (the owner's
second review, 2026-09-29).

A question about a company we do not cover must stop before retrieval. Otherwise "What is
Infosys's FY2025 revenue?", asked after a question about TCS, takes TCS from the conversation and
answers with TCS's figures. Two plain ways of spotting another company:

1. known names: well-known listed companies that are not ours (``_KNOWN_OTHERS``), matched as
   whole words in any case, so "what is infosys revenue" is caught too;
2. a best-effort net for other names: capitalised words ("ICICI Bank", "L&T") count as a
   company only where a company stands in a financial question:
     - before a possessive and a financial word within four words ("Zomato's FY2025 revenue"),
     - right before a financial word, perhaps after a period ("Swiggy revenue"),
     - right after a financial word and "of", "for" or "at" ("the net profit of Zomato"),
     - next to "with", "vs", "versus", "than" or "against", or in an "and"/"or"/comma list of a
       comparison ("compare", "better", "bigger" ...), when the question names another company.
   Never a name right after one of ours ("Reliance's Consolidated net profit", "Reliance Jio's
   ARPU": it qualifies our own figure). So "What did Mukesh Ambani's letter say?" names no
   company: a letter is not a financial word.

The list is the main mechanism; the positions only catch what it misses. Both are kept strict
because refusing a question about our own stocks is worse than what they guard against: an
unknown company the code misses reaches the model with all three stocks (a question that does
not refer back never borrows a stock from the conversation, app/chat/understand.py), and the
model's "out of scope" stands when none of our stocks is named.

A name is ours when it is one of our stocks' names or one of their businesses ("Jio", "Reliance
Retail"). A capitalised word that is not a company (a question word, a month, a period, a ratio,
a regulator, an index: ``_NOT_COMPANIES``) is dropped from either end of a name first. A longer
name that contains one of ours ("HDFC Life", "Reliance Power", "Tata Motors") is another company.
"""

import re

# Our stocks by their names in a question (whole words, any case). "hdfc" alone means HDFC Bank.
_ALIASES = {
    "RELIANCE": r"reliance\s+industries|reliance|ril",
    "TCS": r"tata\s+consultancy\s+services|tata\s+consultancy|tcs",
    "HDFCBANK": r"hdfc\s+bank|hdfcbank|hdfc",
}
_STOCK_NAMES = {
    symbol: re.compile(rf"\b(?:{names})\b", re.IGNORECASE) for symbol, names in _ALIASES.items()
}
# One of our names, possessive or not, right at the end of a text ("Reliance's", "TCS").
_NAME_AT_END = re.compile(rf"\b(?:{'|'.join(_ALIASES.values())})(?:'s?)?\s*$", re.IGNORECASE)
# One of our businesses, the same way ("Reliance Jio's", "Jio").
_BUSINESS_AT_END = re.compile(
    r"\b(?:reliance\s+jio(?:\s+infocomm)?|jio(?:\s+platforms)?|reliance\s+retail(?:\s+ventures)?|"
    r"jiostar|o2c|tcs\s+ion)(?:'s?)?\s*$",
    re.IGNORECASE,
)


def symbols_in(text: str) -> tuple[str, ...]:
    """Our stocks a text names, in order of first mention."""
    found = {
        symbol: match.start()
        for symbol, name in _STOCK_NAMES.items()
        if (match := name.search(text))
    }
    return tuple(sorted(found, key=found.__getitem__))


def ends_with_our_name(text: str) -> bool:
    """True when the text ends with one of our stocks' names ("... Reliance's")."""
    return _NAME_AT_END.search(text.replace("\u2019", "'")) is not None


# Our three stocks as a name may write them (with "ltd", "limited" and the like removed).
_OURS = {
    "reliance",
    "reliance industries",
    "ril",
    "tcs",
    "tata consultancy",
    "tata consultancy services",
    "hdfc bank",
    "hdfcbank",
    "hdfc",
}
# Businesses inside our stocks' consolidated figures: a question about one is about ours.
_OUR_BUSINESSES = {
    "jio",
    "jio platforms",
    "reliance jio",
    "reliance jio infocomm",
    "reliance retail",
    "reliance retail ventures",
    "jiostar",
    "o2c",
    "tcs ion",
}
_SUFFIXES = {"ltd", "ltd.", "limited", "inc", "inc.", "corp", "corp.", "plc", "group", "co", "co."}

# Well-known listed companies that are not ours, found in any case.
_KNOWN_OTHERS = re.compile(
    r"(?<![\w&])(?:infosys|wipro|hcl\s*tech(?:nologies)?|tech\s+mahindra|ltimindtree|mphasis|"
    r"coforge|icici\s+bank|axis\s+bank|kotak(?:\s+mahindra)?\s+bank|state\s+bank\s+of\s+india|"
    r"sbi|indusind\s+bank|yes\s+bank|bank\s+of\s+baroda|punjab\s+national\s+bank|"
    r"bajaj\s+finance|bajaj\s+finserv|hdfc\s+life|hdfc\s+amc|sbi\s+life|itc|"
    r"hindustan\s+unilever|hul|bharti\s+airtel|airtel|vodafone\s+idea|adani\s+\w+|"
    r"tata\s+motors|tata\s+steel|tata\s+power|maruti(?:\s+suzuki)?|larsen\s*&\s*toubro|l&t|"
    r"asian\s+paints|ongc|ntpc|sun\s+pharma\w*|jsw\s+steel|coal\s+india|reliance\s+power|"
    r"reliance\s+capital|reliance\s+infrastructure|jio\s+financial(?:\s+services)?|apple|"
    r"microsoft|alphabet|google|amazon|nvidia|tesla|accenture|ibm|cognizant|capgemini|zomato|"
    r"eternal|swiggy|paytm|one97|nykaa|nestle(?:\s+india)?|hindalco|hindustan\s+zinc|vedanta|"
    r"bajaj\s+auto|hero\s+motocorp|eicher(?:\s+motors)?|dr\.?\s+reddy'?s|cipla|lupin|biocon|"
    r"britannia|dabur|godrej\s+\w+|pidilite|havells|titan|avenue\s+supermarts|dmart|irctc|bpcl|"
    r"gail|grasim|ultratech|zee\s+entertainment)"
    r"(?![\w&])",
    re.IGNORECASE,
)

# Capitalised words that are not companies.
_NOT_COMPANY_WORDS = """
    what which who whom whose how why when where is are was were do does did can could should
    would will compare calculate compute find give tell show explain list summarise summarize
    describe based only please i i'm im my me and also then the a an its it this that these those
    in on for of to if ignore use remember according as so yes no ok hi hello thanks note jan feb
    mar apr may jun jul aug sep sept oct nov dec january february march april june july august
    september october november december monday tuesday wednesday thursday friday saturday sunday
    eps roe roce nim nii npa npas gnpa nnpa pat pbt ebitda ebit casa ldr crar cet1 tcv yoy qoq
    cagr capex opex ai it bfsi us usa uk usd inr rs gst ipo ceo cfo md coo cto esg pe p/e d/e rbi
    sebi bse nse nifty sensex india indian government govt mumbai delhi europe america asia global
    annual report earnings call presentation investor board chairman management
    consolidated standalone gross net total order book digital services retail free cash flow
    debt basel arpu segment segments oil gas operating margin margins ratio revenue profit
"""
_NOT_COMPANIES = frozenset(_NOT_COMPANY_WORDS.split())
_PERIOD_TOKEN = re.compile(r"(?:fy'?\d{2,4}|q[1-4](?:fy\d{2,4})?|[1-4]q\w*|h[12]|9m|\d+)", re.I)

# A run of capitalised words, "&" allowed between them ("Larsen & Toubro", "L&T").
_NAME = re.compile(r"(?<![\w&'])[A-Z][\w&.\-]*(?:\s+(?:&\s+)?[A-Z][\w&.\-]*)*")
_FINANCIAL = (
    r"(?:revenues?|sales|turnover|(?:net\s+)?profits?|earnings|(?:net\s+)?income|eps|ebitda|"
    r"margins?|debt|borrowings?|equity|dividends?|(?:share|stock)\s+prices?|results|"
    r"valuation|market\s+cap\w*|balance\s+sheet|growth|financials?|npas?|roe)\b"
)
# ("Zomato's FY2025 revenue"; not "Mukesh Ambani's share of profit": "of" ends it)
_POSSESSIVE_THEN_FINANCIAL = re.compile(
    rf"^'s?(?:\s+(?!(?:of|on|about|in)\b)[\w&.\-]+){{0,4}}?\s+{_FINANCIAL}", re.IGNORECASE
)
_THEN_FINANCIAL = re.compile(
    rf"^(?:\s+(?:fy\s*'?\d{{2,4}}|q[1-4]\w*|20\d\d))?\s+{_FINANCIAL}", re.IGNORECASE
)
_FINANCIAL_OF_BEFORE = re.compile(rf"{_FINANCIAL}\s+(?:of|for|at)\s+(?:the\s+)?$", re.I)
_VERSUS_BEFORE = re.compile(r"\b(?:with|vs\.?|versus|than|against)\s+(?:the\s+)?$", re.I)
_VERSUS_AFTER = re.compile(r"^\s+(?:with|vs\.?|versus|than|against)\b", re.I)
_LIST_BEFORE = re.compile(r"(?:\b(?:and|or)|,)\s+(?:the\s+)?$", re.I)
_LIST_AFTER = re.compile(r"^(?:\s*,|\s+(?:and|or)\b)", re.I)
_COMPARING = re.compile(
    r"\b(?:compare\w*|comparison|vs\.?|versus|better|worse|bigger|smaller|larger|higher|lower)\b",
    re.IGNORECASE,
)


def _is_word_of_no_company(token: str) -> bool:
    word = token.rstrip(".,").lower()  # "FY2026." at the end of a sentence
    return word in _NOT_COMPANIES or _PERIOD_TOKEN.fullmatch(word) is not None


def _trimmed(name: str) -> str:
    """The name without non-company words at either end, nor a final full stop."""
    tokens = name.rstrip(".-").split()
    while tokens and _is_word_of_no_company(tokens[0]):
        tokens.pop(0)
    while tokens and _is_word_of_no_company(tokens[-1]):
        tokens.pop()
    return " ".join(tokens)


def _is_ours(name: str) -> bool:
    words = [word for word in name.lower().split() if word not in _SUFFIXES]
    plain = " ".join(words)
    return plain in _OURS or plain in _OUR_BUSINESSES


def _after_ours(before: str) -> bool:
    """Right after one of our names or businesses: a word there qualifies our own figure."""
    before = before.replace("\u2019", "'")
    return _NAME_AT_END.search(before) is not None or _BUSINESS_AT_END.search(before) is not None


def _in_company_position(question: str, start: int, end: int, in_list: bool) -> bool:
    before, after = question[:start], question[end:]
    if _after_ours(before):
        return False
    versus = _VERSUS_BEFORE.search(before) or _VERSUS_AFTER.match(after)
    listed = _COMPARING.search(question) and (
        _LIST_BEFORE.search(before) or _LIST_AFTER.match(after)
    )
    return bool(
        _POSSESSIVE_THEN_FINANCIAL.match(after)
        or _THEN_FINANCIAL.match(after)
        or _FINANCIAL_OF_BEFORE.search(before)
        or (in_list and (versus or listed))
    )


def other_companies(question: str) -> tuple[str, ...]:
    """The companies the question names that are not ours, as it writes them, in order, each
    once (compared in any case)."""
    text = question.replace("\u2019", "'")
    found: list[tuple[int, str]] = [(m.start(), m.group()) for m in _KNOWN_OTHERS.finditer(text)]
    names = [m for m in _NAME.finditer(text) if _trimmed(m.group())]
    in_list = bool(symbols_in(text)) or len(names) > 1
    for match in names:
        name = _trimmed(match.group())
        start = match.start() + match.group().index(name)
        if _is_ours(name):
            continue
        if _in_company_position(text, start, start + len(name), in_list):
            found.append((start, name))
    unique: dict[str, str] = {}
    for _, name in sorted(found):
        unique.setdefault(name.lower(), name)
    return tuple(unique.values())
