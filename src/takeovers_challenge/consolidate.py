"""From per-document extractions to one deduplicated, grounded list of movements.

The same capital increase is typically found once as a decision and five more times as restated history in later
statutes. This module grounds every candidate in its OCR lines, clusters candidates that describe the same movement,
picks the best-supported one as the source, and records where the documents disagree.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz

from . import bbox
from .corpus import Document
from .schemas import DocExtraction, Evidence, ExtractedEvent

MOVEMENT_STATUSES = {"completed_here", "realisation_of_earlier_decision", "decided_pending_realisation", "restated_history"}
STATUS_RANK = {"completed_here": 0, "realisation_of_earlier_decision": 0, "decided_pending_realisation": 2, "restated_history": 3}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
GROUNDING_THRESHOLD = 80

_TITLES = re.compile(r"\b(MONSIEUR|MADAME|MADEMOISELLE|MR|MME|MLLE|M|LA SOCIETE|SOCIETE|SAS|SARL|SA|STE)\b\.?")


def normalize_name(name: str | None, aliases: dict[str, str] | None = None) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper()
    text = _TITLES.sub(" ", re.sub(r"[^A-Z0-9 ]", " ", text))
    text = " ".join(text.split())
    for alias, canonical in (aliases or {}).items():
        if normalize_name(alias) == text:
            return normalize_name(canonical)
    return text


_CLASS_NOISE = {"ACTION", "ACTIONS", "DE", "DES", "DITES", "PREFERENCE", "PREFERENCES", "CATEGORIE", "CATEGORIES",
                "CLASSE", "CLASS", "SHARES", "ET", "AND", "NOUVELLES", "ORDINAIRES"}


def class_tokens(name: str | None) -> frozenset[str]:
    """The distinctive part of a share-class name: 'actions de préférence de catégorie B et B'' -> {'B'}."""
    return frozenset(t for t in normalize_name(name).split() if t not in _CLASS_NOISE)


def same_party(a: str | None, b: str | None, aliases: dict[str, str] | None = None) -> bool:
    na, nb = normalize_name(a, aliases), normalize_name(b, aliases)
    return bool(na and nb) and fuzz.token_set_ratio(na, nb) >= 90


def _days_apart(a: str | None, b: str | None) -> int | None:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


@dataclass
class Grounding:
    inpi_id: str
    page: int | None
    bbox: list[float] | None
    snippet: str
    line_ids: list[str]
    score: float
    grounded: bool
    note: str | None = None


def ground(doc: Document, evidence: Evidence) -> Grounding:
    """Box = union of the cited lines on the page holding most of them; snippet = the OCR text of those lines."""
    hits = [(line_id, doc.lookup(line_id)) for line_id in evidence.line_ids]
    valid = [hit for _, hit in hits if hit]
    missing = [line_id for line_id, hit in hits if not hit]
    if not valid:
        return Grounding(doc.inpi_id, None, None, evidence.quote_fr, [], 0.0, False, "no cited line id exists in the OCR")

    page_no = Counter(page.number for page, _ in valid).most_common(1)[0][0]
    on_page = [(page, line) for page, line in valid if page.number == page_no]
    snippet = " ".join(line.text for _, line in on_page)
    all_text = " ".join(line.text for _, line in valid)
    score = max(fuzz.partial_ratio(evidence.quote_fr, snippet), fuzz.token_set_ratio(evidence.quote_fr, all_text))
    notes = []
    if missing:
        notes.append(f"unknown line ids {missing}")
    if len(valid) != len(on_page):
        notes.append("evidence spans pages; box covers the page with most cited lines")
    grounded = score >= GROUNDING_THRESHOLD
    if not grounded:
        notes.append(f"quote does not match cited OCR lines (similarity {score:.0f})")
    return Grounding(
        inpi_id=doc.inpi_id,
        page=page_no,
        bbox=bbox.rounded(bbox.union(page.norm_box(line) for page, line in on_page)),
        snippet=snippet,
        line_ids=[line.id for _, line in on_page],
        score=round(score, 1),
        grounded=grounded,
        note="; ".join(notes) or None,
    )


@dataclass
class Candidate:
    doc: Document
    event: ExtractedEvent
    grounding: Grounding

    @property
    def rank(self) -> tuple:
        return (
            STATUS_RANK[self.event.status],
            not self.grounding.grounded,
            CONFIDENCE_RANK[self.event.confidence],
            self.doc.deposit_date,
        )


@dataclass
class Movement:
    """One real-world movement, possibly described by several documents."""

    candidates: list[Candidate]
    conflicts: list[str] = field(default_factory=list)
    event_id: str = ""

    @property
    def primary(self) -> Candidate:
        return min(self.candidates, key=lambda c: c.rank)

    @property
    def code(self) -> str:
        return self.primary.event.code

    @property
    def effective_date(self) -> str | None:
        if self.primary.event.effective_date:
            return self.primary.event.effective_date
        dated = sorted((c for c in self.candidates if c.event.effective_date), key=lambda c: c.rank)
        return dated[0].event.effective_date if dated else None

    @property
    def confirmed(self) -> bool:
        """Pending decisions count only if some document shows them carried out."""
        return any(c.event.status != "decided_pending_realisation" for c in self.candidates)

    def value(self, attr: str):
        """The primary's value, falling back to the best-ranked candidate that has one."""
        for cand in sorted(self.candidates, key=lambda c: c.rank):
            if (v := getattr(cand.event, attr)) not in (None, [], ""):
                return v
        return None


def _same_movement(a: ExtractedEvent, b: ExtractedEvent, aliases: dict[str, str]) -> bool:
    if a.code != b.code:
        return False
    gap = _days_apart(a.effective_date, b.effective_date)
    if a.code in ("CAPITAL_INCREASE", "CAPITAL_DECREASE"):
        if a.capital_after_eur is not None and b.capital_after_eur is not None:
            return round(a.capital_after_eur) == round(b.capital_after_eur) and (gap is None or gap <= 400)
        if (a.capital_after_eur is None and a.amount_eur is None) or (b.capital_after_eur is None and b.amount_eur is None):
            return gap == 0  # a mention without figures joins the same-day movement it refers to
        return a.amount_eur is not None and a.amount_eur == b.amount_eur and (gap is None or gap <= 60)
    if a.code == "SHAREHOLDER_SHARE_TRANSFER":
        return (
            same_party(a.from_name, b.from_name, aliases)
            and same_party(a.to_name, b.to_name, aliases)
            and (a.shares is None or b.shares is None or a.shares == b.shares)
            and (gap is None or gap <= 400)
        )
    if a.code in ("SHAREHOLDER_ENTRY", "SHAREHOLDER_END"):
        return same_party(a.holder_name, b.holder_name, aliases) and (gap is None or gap <= 400)
    if a.code == "CAPITAL_DUAL_CLASS":
        ca, cb = class_tokens(a.share_class), class_tokens(b.share_class)
        return gap is not None and gap <= 60 and (not (ca and cb) or ca == cb)
    return False


def _conflicts(movement: Movement) -> list[str]:
    out = []
    checks = ["effective_date", "amount_eur", "capital_after_eur", "shares", "shares_delta", "price_per_share_eur"]
    for attr in checks:
        seen: dict[object, list[str]] = {}
        for cand in movement.candidates:
            v = getattr(cand.event, attr)
            if v is not None:
                seen.setdefault(v, []).append(f"{cand.doc.inpi_id} p{cand.grounding.page}")
        if len(seen) > 1:
            detail = "; ".join(f"{v} ({', '.join(where)})" for v, where in seen.items())
            out.append(f"{attr} disagrees across documents: {detail}")
    return out


def consolidate(
    extractions: list[tuple[Document, DocExtraction]], aliases: dict[str, str] | None = None
) -> tuple[list[Movement], dict]:
    aliases = aliases or {}
    stats = Counter()
    movements: list[Movement] = []
    for doc, extraction in extractions:
        for event in extraction.events:
            stats[event.status] += 1
            if event.status not in MOVEMENT_STATUSES:
                continue
            cand = Candidate(doc, event, ground(doc, event.evidence))
            stats["grounded" if cand.grounding.grounded else "ungrounded"] += 1
            match = next((m for m in movements if any(_same_movement(event, c.event, aliases) for c in m.candidates)), None)
            if match:
                match.candidates.append(cand)
            else:
                movements.append(Movement([cand]))

    for m in movements:
        m.conflicts = _conflicts(m)
    movements.sort(key=lambda m: (m.effective_date or "9999", m.code))
    for i, m in enumerate(movements, 1):
        m.event_id = f"ev{i:03d}"
    return movements, dict(stats)
