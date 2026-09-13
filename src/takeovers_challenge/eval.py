"""Score a model's extractions against the hand labels, at the level that matters: events, grounding, timeline.

Both sides go through the same overrides, consolidation and fold, so differences come from extraction alone.
"""

from __future__ import annotations

import json

from . import config, export, extract
from .consolidate import class_tokens, normalize_name, same_party

CAPITAL_CODES = ("CAPITAL_INCREASE", "CAPITAL_DECREASE")


def same_class(a: str | None, b: str | None) -> bool:
    ta, tb = class_tokens(a), class_tokens(b)
    return not (ta and tb) or ta == tb


def _same_movement(g: dict, t: dict) -> bool:
    if g["event_code"] != t["event_code"]:
        return False
    gp, tp, code = g["payload"], t["payload"], g["event_code"]
    if code in CAPITAL_CODES:
        return gp.get("capital_after_eur") is not None and gp.get("capital_after_eur") == tp.get("capital_after_eur")
    if code == "SHAREHOLDER_SHARE_TRANSFER":
        return same_party(gp.get("from_name"), tp.get("from_name")) and same_party(gp.get("to_name"), tp.get("to_name"))
    if code in ("SHAREHOLDER_ENTRY", "SHAREHOLDER_END"):
        return same_party(gp.get("holder_name"), tp.get("holder_name"))
    return g["event_date"] == t["event_date"] and same_class(gp.get("class_name"), tp.get("class_name"))


def _iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _label(e: dict) -> str:
    p = e["payload"]
    who = p.get("holder_name") or " -> ".join(filter(None, [p.get("from_name"), p.get("to_name")])) or p.get("class_name") or ""
    value = p.get("capital_after_eur") or p.get("shares") or ""
    return f"{e['event_date']} {e['event_code']} {who} {value}".strip()


def compare_events(gold: list[dict], test: list[dict]) -> dict:
    unmatched_test = list(test)
    matched, missed, date_diff, value_diff, same_page, ious = [], [], [], [], 0, []
    for g in gold:
        candidates = [t for t in unmatched_test if _same_movement(g, t)]
        if not candidates:
            missed.append(_label(g))
            continue
        t = next((t for t in candidates if t["event_date"] == g["event_date"]), candidates[0])
        unmatched_test.remove(t)
        matched.append((g, t))
        if g["event_date"] != t["event_date"]:
            date_diff.append(f"{_label(g)}: model says {t['event_date']}")
        for key in ("amount_eur", "shares", "capital_after_eur"):
            if g["payload"].get(key) is not None and g["payload"].get(key) != t["payload"].get(key):
                value_diff.append(f"{_label(g)}: {key} {g['payload'].get(key)} vs {t['payload'].get(key)}")
        if (g["source"]["inpi_id"], g["source"]["page"]) == (t["source"]["inpi_id"], t["source"]["page"]):
            same_page += 1
            ious.append(_iou(g["source"]["bbox"], t["source"]["bbox"]))
    n = len(matched)
    return {
        "gold": len(gold),
        "model": len(test),
        "matched": n,
        "recall": round(n / len(gold), 3) if gold else None,
        "precision": round(n / len(test), 3) if test else None,
        "date_mismatches": date_diff,
        "value_mismatches": value_diff,
        "missed": missed,
        "extra": [_label(t) for t in unmatched_test],
        "grounded_rate": round(sum(1 for t in test if t["source"].get("grounded", True)) / len(test), 3) if test else None,
        "same_source_page_rate": round(same_page / n, 3) if n else None,
        "mean_bbox_iou_on_same_page": round(sum(ious) / len(ious), 3) if ious else None,
    }


def _holders(state: dict) -> dict:
    return dict(sorted((normalize_name(h["name"]), h["shares"]) for h in state["holders"]))


def compare_timelines(gold: list[dict], test: list[dict]) -> dict:
    remaining, exact, diffs = list(test), 0, []
    for g in gold:
        same_point = [t for t in remaining if (t["as_of"], t["capital_eur"]) == (g["as_of"], g["capital_eur"])]
        if not same_point:
            diffs.append(f"{g['as_of']} capital {g['capital_eur']:,.0f}: no model state")
            continue
        t = next((t for t in same_point if _holders(t) == _holders(g) and t["shares_total"] == g["shares_total"]), same_point[0])
        remaining.remove(t)
        parts = []
        if t["shares_total"] != g["shares_total"]:
            parts.append(f"shares_total {g['shares_total']} vs {t['shares_total']}")
        if _holders(t) != _holders(g):
            parts.append(f"holders {_holders(g)} vs {_holders(t)}")
        if parts:
            diffs.append(f"{g['as_of']} capital {g['capital_eur']:,.0f}: " + "; ".join(parts))
        else:
            exact += 1
    extra = [f"{t['as_of']} capital {t['capital_eur']:,.0f}: {_holders(t)}" for t in remaining]
    return {"gold_states": len(gold), "model_states": len(test), "exact_matches": exact, "diffs": diffs, "extra_states": extra}


def run(model: str) -> dict:
    gold, _ = export.build("manual")
    test, summary = export.build(model)
    report = {
        "model": model,
        "events": compare_events(gold["events"], test["events"]),
        "timeline": compare_timelines(gold["capital_timeline"], test["capital_timeline"]),
        "pipeline_summary": summary,
        "usage": extract.usage_report(model),
    }
    out = config.REPO_ROOT / "eval" / f"{model}.{extract.PROMPT_VERSION}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
