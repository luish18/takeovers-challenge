"""Hand-labelled extractions (labels/manual.yaml) in the same schema the model returns.

Used as the gold set to score model runs, and as an extraction source of its own (`actes build --model manual`).
Labels only give line ids for evidence; the French quote is filled from the OCR, and omitted nullable fields are null.
"""

from __future__ import annotations

import datetime as dt
from functools import cache

import yaml

from . import config
from .corpus import Document
from .schemas import CapitalSnapshot, DocExtraction, ExtractedEvent, HolderStake

LABELS_PATH = config.REPO_ROOT / "labels" / "manual.yaml"


def _plain(value):
    """YAML turns 2005-05-17 into a date; the schema wants strings."""
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


@cache
def _labels() -> dict:
    if not LABELS_PATH.exists():
        return {}
    return _plain(yaml.safe_load(LABELS_PATH.read_text(encoding="utf-8")) or {})


def _nulls(model) -> dict:
    return {name: None for name, info in model.model_fields.items() if not info.is_required() or "None" in str(info.annotation)}


def _evidence(doc: Document, line_ids: list[str]) -> dict:
    missing = [i for i in line_ids if doc.lookup(i) is None]
    if missing:
        raise ValueError(f"labels for {doc.inpi_id} cite unknown lines {missing}")
    return {"line_ids": line_ids, "quote_fr": " ".join(doc.lookup(i)[1].text for i in line_ids)}


def _holder(raw: dict, default_kind: str = "UNKNOWN") -> dict:
    return {**_nulls(HolderStake), "kind": default_kind, **raw}


def load(doc: Document) -> DocExtraction | None:
    raw = _labels().get(doc.inpi_id)
    if raw is None:
        return None
    events = []
    for e in raw.get("events") or []:
        full = {**_nulls(ExtractedEvent), "subscribers": [], "date_basis": "", "gloss_en": "", "confidence": "high"}
        full.update({k: v for k, v in e.items() if k != "evidence"})
        full["subscribers"] = [_holder(s, "PERSON") for s in full["subscribers"]]
        full["evidence"] = _evidence(doc, e["evidence"])
        events.append(full)
    snapshots = []
    for s in raw.get("snapshots") or []:
        full = {**_nulls(CapitalSnapshot), "as_of_basis": "", "holders": [], "holders_complete": False}
        full.update({k: v for k, v in s.items() if k != "evidence"})
        full["holders"] = [_holder(h) for h in full["holders"]]
        full["evidence"] = _evidence(doc, s["evidence"])
        snapshots.append(full)
    decisions = [{**d, "evidence": _evidence(doc, d["evidence"])} for d in raw.get("decision_dates") or []]
    return DocExtraction.model_validate(
        {
            "summary_en": raw.get("summary_en", ""),
            "decision_dates": decisions,
            "events": events,
            "snapshots": snapshots,
            "issues": raw.get("issues") or [],
        }
    )
