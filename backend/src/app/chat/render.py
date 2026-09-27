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
"""

from app.chat.answer_check import Claim, without_markers
from app.chat.contract import Source
from app.chat.evidence import EvidenceItem
from app.retrieval import excerpt


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
