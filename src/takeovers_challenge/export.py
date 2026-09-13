"""Assemble results.json: load extractions, apply overrides, consolidate, fold, validate against the challenge schema."""

from __future__ import annotations

import json
from collections import Counter

import jsonschema

from . import config, corpus, extract, overrides
from .consolidate import Grounding, Movement, consolidate
from .timeline import UNIDENTIFIED, DerivedEvent, Folder, State, collect_snapshots

CAPITAL_CODES = ("CAPITAL_INCREASE", "CAPITAL_DECREASE")


def _compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, [], "")}


def _source(g: Grounding) -> dict:
    src = {
        "inpi_id": g.inpi_id,
        "page": g.page or 1,
        "bbox": g.bbox or [0.0, 0.0, 1.0, 1.0],
        "snippet": g.snippet,
    }
    if not g.grounded:
        src["grounded"] = False
    if g.note:
        src["note"] = g.note
    return src


def movement_event(m: Movement) -> dict:
    get, code, primary = m.value, m.code, m.primary.event
    if code in CAPITAL_CODES:
        payload = {
            "amount_eur": get("amount_eur"),
            "capital_after_eur": get("capital_after_eur"),
            "method": get("method") or "autre",
            "capital_before_eur": get("capital_before_eur"),
            "shares": abs(get("shares_delta")) if get("shares_delta") is not None else None,
            "nominal_eur": get("nominal_eur"),
            "share_class": get("share_class"),
            "price_per_share_eur": get("price_per_share_eur"),
            "mechanism": get("mechanism"),
            "subscribers": [_compact({"name": s.name, "kind": s.kind, "siren": s.siren, "shares": s.shares}) for s in get("subscribers") or []],
        }
    elif code == "SHAREHOLDER_SHARE_TRANSFER":
        shares, price = get("shares"), get("price_per_share_eur")
        payload = {
            "from_name": get("from_name"),
            "to_name": get("to_name"),
            "shares": shares,
            "price_eur": round(shares * price, 2) if shares and price else None,
            "price_per_share_eur": price,
            "mechanism": get("mechanism"),
        }
    elif code in ("SHAREHOLDER_ENTRY", "SHAREHOLDER_END"):
        payload = {
            "holder_name": get("holder_name"),
            "holder_siren": get("holder_siren"),
            "shares": get("shares"),
            "holder_kind": get("holder_kind"),
            "mechanism": get("mechanism"),
        }
    else:
        payload = {"class_name": get("share_class"), "description": get("mechanism") or primary.gloss_en}

    payload |= {
        "status": primary.status,
        "date_basis": primary.date_basis,
        "confidence": primary.confidence,
        "gloss_en": primary.gloss_en,
        "notes": primary.notes,
        "conflicts": m.conflicts,
        "corroborated_by": [
            {**_source(c.grounding), "status": c.event.status}
            for c in sorted(m.candidates, key=lambda c: c.doc.deposit_date)
            if c is not m.primary
        ],
    }
    return {
        "event_id": m.event_id,
        "event_code": code,
        "event_date": m.effective_date,
        "payload": _compact(payload),
        "source": _source(m.primary.grounding),
    }


def derived_event(d: DerivedEvent) -> dict:
    payload = _compact({**d.payload, "derived": True, "mechanism_event_ids": d.mechanism_event_ids, "notes": d.note})
    return {"event_id": d.event_id, "event_code": d.code, "event_date": d.date, "payload": payload, "source": _source(d.grounding)}


def state_entry(s: State) -> dict:
    holders = []
    for h in sorted(s.holdings.values(), key=lambda h: (h.name.startswith(UNIDENTIFIED), -(h.shares or 0), h.name)):
        pct = round(100 * h.shares / s.shares_total, 2) if h.shares is not None and s.shares_total else None
        holders.append(
            {"name": h.name, "siren": h.siren, "kind": h.kind, "shares": h.shares, "pct": pct}
            | _compact({"share_class": h.share_class, "note": h.note})
        )
    return {
        "as_of": s.as_of,
        "capital_eur": s.capital_eur,
        "shares_total": s.shares_total,
        "nominal_eur": s.nominal_eur,
        "holders": holders,
        "caused_by": s.caused_by + [d.event_id for d in s.derived],
        "checks": s.checks,
        "notes": s.notes,
        "evidence": [_source(ref.grounding) for ref in s.evidence],
    }


def _bullets(title: str, items: list[str]) -> str | None:
    return f"{title}\n- " + "\n- ".join(items) if items else None


def build(source: str) -> tuple[dict, dict]:
    docs = corpus.load_subject_corpus()
    ov = overrides.load()
    extractions, missing = [], []
    for doc in docs:
        extraction = extract.load_extraction(doc, source)
        if extraction is None:
            missing.append(f"{doc.deposit_date} {doc.inpi_id} ({doc.meta.get('denomination')})")
        else:
            extractions.append((doc, extraction))

    override_log = overrides.apply(extractions, ov)
    movements, stats = consolidate(extractions, ov.aliases)
    folder = Folder(ov.aliases)
    states = folder.fold(movements, collect_snapshots(extractions))

    derived = [d for s in states for d in s.derived]
    for i, d in enumerate(derived, 1):
        d.event_id = f"dv{i:03d}"
    kept = [m for m in movements if m.confirmed and m.effective_date and m.event_id not in folder.skipped]
    events = sorted([movement_event(m) for m in kept] + [derived_event(d) for d in derived], key=lambda e: (e["event_date"], e["event_id"]))

    disagreements = [f"{m.event_id} {m.code} ({m.effective_date}): {c}" for m in kept for c in m.conflicts]
    disagreements += [f"cap table on {s.as_of}: {c}" for s in states for c in s.checks]
    by_id = {m.event_id: m for m in movements}
    skipped = [
        f"{event_id} {by_id[event_id].code} ({by_id[event_id].effective_date}, {by_id[event_id].primary.doc.inpi_id}): {reason}"
        for event_id, reason in folder.skipped.items()
    ]
    doc_issues = [f"{doc.deposit_date} {doc.inpi_id}: {issue}" for doc, ex in extractions for issue in ex.issues]
    notes = "\n\n".join(
        filter(
            None,
            [
                f"Extraction source: {source} (prompt {extract.PROMPT_VERSION}). {len(extractions)} documents read, "
                f"{len(kept)} movements after deduplicating {sum(len(m.candidates) for m in movements)} mentions, "
                f"{len(derived)} entries/exits/transfers derived from consecutive cap tables.",
                _bullets("Where the documents disagree, or the arithmetic does not hold:", disagreements),
                _bullets("Left out of the timeline:", folder.issues),
                _bullets("Mentions not applied to the cap table (and not exported as events):", skipped),
                _bullets("Manual adjudications (overrides.yaml):", override_log),
                _bullets("Per-document observations:", doc_issues),
                _bullets("No extraction available for:", missing),
            ],
        )
    )

    results = {
        "siren": config.SIREN,
        "events": events,
        "capital_timeline": [state_entry(s) for s in states],
        "notes": notes,
        "pipeline": {
            "extraction_source": source,
            "prompt_version": extract.PROMPT_VERSION,
            "documents": [{"inpi_id": d.inpi_id, "siren": d.meta["siren"], "deposit_date": d.deposit_date} for d, _ in extractions],
            "cross_reference_documents": config.CROSS_REFERENCE_DOCS,
        },
    }
    schema = json.loads((config.SCHEMA_DIR / "results.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(results)

    summary = {
        "mentions": dict(stats),
        "events": dict(Counter(e["event_code"] for e in events)),
        "derived": len(derived),
        "states": len(states),
        "checks": sum(len(s.checks) for s in states),
        "missing": len(missing),
    }
    return results, summary


def main(model: str) -> int:
    results, summary = build(model)
    config.RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for s in results["capital_timeline"]:
        holders = ", ".join(f"{h['name']} {h['shares']}" for h in s["holders"])
        flag = f"  [{len(s['checks'])} check(s)]" if s["checks"] else ""
        print(f"{s['as_of']}  {s['capital_eur'] or 0:>10,.0f} EUR  {s['shares_total'] or 0:>8,} sh  | {holders}{flag}")
    print(f"wrote {config.RESULTS_PATH}")
    return 0
