"""From checked claims to the answer the user reads (P12; the project notes "RAG and citations").

The model cites evidence IDs ("F1", "D2", "N3"); the reader sees numbered markers "[1][2]" and,
under the answer, one source per marker. The sources come from the stored evidence items, never
from the model: the model cannot invent a label, a link or a quote.

- markers are numbered 1, 2, 3 in order of first use across the whole answer; an item cited
  again reuses its number;
- each claim is its text (any IDs the model wrote into it removed) followed by its markers,
  and the claims are joined with one space;
- a source's quote is cut to a short excerpt (app/retrieval.py's excerpt, about 300 characters).

Only a checked answer is rendered (app/chat/answer_check.py): an unknown ID here is a bug in
the caller, so it raises instead of being skipped.

disclosures (the owner's review, 2026-09-29): when a cited figure has another source's differing
figure (its ``rivals``) and the answer does not cite it, code adds one sentence naming it, cited
to that source, so a figure is never chosen silently. The sentence gives what each source calls
its figure (the second review): they are never assumed to measure the same thing.
"""

from app.chat.answer_check import Claim, without_markers
from app.chat.contract import Source
from app.chat.evidence import EvidenceItem, source_word
from app.retrieval import excerpt


def disclosures(claims: list[Claim], evidence: list[EvidenceItem]) -> list[Claim]:
    """One sentence for each rival of a cited figure that the answer does not cite itself."""
    by_id = {item.id: item for item in evidence}
    cited = list(dict.fromkeys(cid for claim in claims for cid in claim.citations))
    added: list[Claim] = []
    for cid in cited:
        for rid in by_id[cid].rivals if cid in by_id else ():
            rival = by_id[rid]
            if rid in cited or rival.metric is None:
                continue
            added.append(Claim(text=_disclosure(by_id[cid], rival), citations=(rid,)))
    return added


def every_disclosure(claims: list[Claim], evidence: list[EvidenceItem]) -> list[Claim]:
    """For a question about disagreeing sources: every stored figure that another source
    disputes, disclosed whether the answer cites it or not (each once)."""
    cited = {cid for claim in claims for cid in claim.citations}
    by_id = {item.id: item for item in evidence}
    return [
        Claim(text=_disclosure(item, by_id[rid]), citations=(rid,))
        for item in evidence
        for rid in item.rivals
        if rid not in cited
    ]


def _called(item: EvidenceItem) -> str:
    return f' (reported as "{item.reported_as}")' if item.reported_as else ""


def _disclosure(main: EvidenceItem, rival: EvidenceItem) -> str:
    """'screener.in gives ₹899 crore for FY2024 (reported as "Sales"), against ₹914 crore in the
    annual report (reported as "Revenue from Operations"); ...'."""
    main_source = source_word(main.label)
    main_source = main_source if main_source.startswith("screener") else main_source.lower()
    return (
        f"{source_word(rival.label)} gives {rival.amount} for {rival.period}{_called(rival)}, "
        f"against {main.amount} in the {main_source}{_called(main)}; the stored data does not "
        "establish that the two measure the same thing, so they are not treated as "
        "interchangeable."
    )


def render(claims: list[Claim], evidence: list[EvidenceItem]) -> tuple[str, tuple[Source, ...]]:
    by_id = {item.id: item for item in evidence}
    markers: dict[str, int] = {}  # evidence ID -> its marker number, in order of first use
    parts: list[str] = []
    for claim in claims:
        numbers: list[int] = []
        for cid in dict.fromkeys(claim.citations):  # each ID once per claim
            if cid not in by_id:
                raise ValueError("render needs a checked answer: it cites an unknown id")
            numbers.append(markers.setdefault(cid, len(markers) + 1))
        text = without_markers(claim.text).strip()
        marks = "".join(f"[{n}]" for n in numbers)
        parts.append(f"{text} {marks}".rstrip())
    sources = tuple(_source(marker, by_id[cid]) for cid, marker in markers.items())
    return " ".join(parts), sources


def _source(marker: int, item: EvidenceItem) -> Source:
    return Source(
        marker=marker,
        source=item.source,
        label=item.label,
        url=item.url,
        quote=excerpt(item.quote) if item.quote is not None else None,
    )
