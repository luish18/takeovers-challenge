"""Human adjudications from overrides.yaml, applied to extracted events before consolidation.

Every rule carries a reason, and the log of what matched ends up in results.json notes, so no hand-made call is hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from . import config
from .consolidate import same_party
from .corpus import Document
from .schemas import DocExtraction, ExtractedEvent

NAME_FIELDS = ("holder_name", "from_name", "to_name")


@dataclass
class Overrides:
    aliases: dict[str, str] = field(default_factory=dict)
    rules: list[dict] = field(default_factory=list)


def load(path=config.OVERRIDES_PATH) -> Overrides:
    if not path.exists():
        return Overrides()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Overrides(raw.get("aliases") or {}, raw.get("events") or [])


def _matches(match: dict, doc: Document, event: ExtractedEvent, aliases: dict[str, str]) -> bool:
    for key, want in match.items():
        if key == "inpi_id":
            if not doc.inpi_id.endswith(str(want)):
                return False
        elif key in NAME_FIELDS:
            if not same_party(getattr(event, key), str(want), aliases):
                return False
        elif str(getattr(event, key)) != str(want):
            return False
    return True


def apply(extractions: list[tuple[Document, DocExtraction]], overrides: Overrides) -> list[str]:
    log = []
    for rule in overrides.rules:
        action, hits = rule.get("action", "set"), 0
        for doc, extraction in extractions:
            kept = []
            for event in extraction.events:
                if _matches(rule["match"], doc, event, overrides.aliases):
                    hits += 1
                    if action == "drop":
                        continue
                    notes = "; ".join(filter(None, [event.notes, f"override: {rule['reason']}"]))
                    event = event.model_copy(update={**rule.get("set", {}), "notes": notes})
                kept.append(event)
            extraction.events = kept
        change = "dropped" if action == "drop" else f"set {rule.get('set')}"
        log.append(f"{rule['match']}: {change} on {hits} event(s){' [NO MATCH]' if not hits else ''}. Reason: {rule['reason']}")
    return log
