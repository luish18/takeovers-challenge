"""Per-document LLM extraction, cached on disk so reruns are free and `build` needs no API key.

The model answers by calling a `record_extraction` tool whose input schema is DocExtraction. Strict structured outputs
reject this schema ("compiled grammar is too large"), and forcing the tool call would disable thinking, so the call is
requested in the prompt, validated with Pydantic, and retried once with the validation errors.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import anthropic
from pydantic import ValidationError

from . import config
from .corpus import Document
from .schemas import DocExtraction

# extract_v1: OCR lines as `[p3.12] text`. extract_v2: adds each line's position and rules learned from scoring v1.
PROMPT_VERSION = os.getenv("ACTES_PROMPT_VERSION", "extract_v2")
PROMPTS_DIR = Path(__file__).parent / "prompts"
TOOL_NAME = "record_extraction"
MAX_ATTEMPTS = 2

# $ per million tokens (input, output), for the cost report only.
PRICES = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-5": (5.00, 25.00),
}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class ExtractionError(RuntimeError):
    pass


def _inline_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = walk(defs[node["$ref"].rsplit("/", 1)[-1]])
                return {**target, **{k: walk(v) for k, v in node.items() if k != "$ref"}}
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


TOOL = {
    "name": TOOL_NAME,
    "description": "Record what this filing says about the subject company's capital composition. Call it exactly once, after reading the whole filing.",
    "input_schema": _inline_refs(DocExtraction.model_json_schema()),
}


def system_prompt() -> str:
    codes = json.loads((config.SCHEMA_DIR / "event_codes.json").read_text(encoding="utf-8"))["codes"]
    rendered = "\n\n".join(
        f"### {c['event_code']}{'' if c['scored'] else ' (not scored)'}\n{c['description']}\n"
        f"Payload fields: {json.dumps(c['payload_fields'], ensure_ascii=False)}"
        for c in codes
    )
    template = (PROMPTS_DIR / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
    return template.replace("{event_codes}", rendered)


def user_message(doc: Document) -> str:
    labels = "; ".join(doc.filing_types) or "none"
    header = (
        f"Subject company: {config.SUBJECT_NAME} (SIREN {config.SIREN})\n"
        f"Filed by: {doc.meta.get('denomination')} (SIREN {doc.meta['siren']})\n"
        f"Filing id: {doc.inpi_id}\n"
        f"Deposited at the greffe: {doc.deposit_date}\n"
        f"Registry labels: {labels}\n"
    )
    return f"{header}\n{doc.render(positions=PROMPT_VERSION != 'extract_v1')}"


def cache_path(inpi_id: str, model: str) -> Path:
    return config.CACHE_DIR / model / PROMPT_VERSION / f"{inpi_id}.json"


def load_extraction(doc: Document, source: str) -> DocExtraction | None:
    """`source` is a model name (cached responses) or "manual" (hand labels)."""
    if source == "manual":
        from . import manual

        return manual.load(doc)
    return load_cached(doc.inpi_id, source)


def load_cached(inpi_id: str, model: str) -> DocExtraction | None:
    path = cache_path(inpi_id, model)
    if not path.exists():
        return None
    return DocExtraction.model_validate(json.loads(path.read_text(encoding="utf-8"))["extraction"])


def extract(
    doc: Document,
    model: str = config.EXTRACT_MODEL,
    *,
    client: anthropic.Anthropic | None = None,
    refresh: bool = False,
) -> DocExtraction:
    if not refresh and (hit := load_cached(doc.inpi_id, model)) is not None:
        return hit

    client = client or anthropic.Anthropic()
    started = time.monotonic()
    messages: list[dict] = [{"role": "user", "content": user_message(doc)}]
    usage = dict.fromkeys(USAGE_KEYS, 0)
    parsed, request_ids, last_problem = None, [], "no tool call"

    for _ in range(MAX_ATTEMPTS):
        # Streaming: statutes-heavy filings produce long tool input plus thinking.
        with client.messages.stream(
            model=model,
            max_tokens=64000,
            system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
            tools=[TOOL],
            messages=messages,
        ) as stream:
            message = stream.get_final_message()
            request_id = getattr(stream, "request_id", None) or getattr(stream.response, "headers", {}).get("request-id")
        request_ids.append(request_id)
        for key in USAGE_KEYS:
            usage[key] += getattr(message.usage, key, 0) or 0
        if message.stop_reason in ("max_tokens", "refusal"):
            raise ExtractionError(f"{doc.inpi_id}: stop_reason={message.stop_reason} ({request_id})")

        call = next((b for b in message.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
        messages.append({"role": "assistant", "content": message.content})
        if call is None:
            last_problem = f"no {TOOL_NAME} call (stop_reason={message.stop_reason})"
            messages.append({"role": "user", "content": f"Call {TOOL_NAME} with your extraction now."})
            continue
        try:
            parsed = DocExtraction.model_validate(call.input)
            break
        except ValidationError as err:
            last_problem = f"{err.error_count()} validation error(s), first: {err.errors()[0]['loc']} {err.errors()[0]['msg']}"
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "is_error": True,
                    "content": f"The input does not match the schema:\n{err}\nCall {TOOL_NAME} again with a corrected input.",
                }],
            })

    if parsed is None:
        raise ExtractionError(f"{doc.inpi_id}: no valid {TOOL_NAME} call after {MAX_ATTEMPTS} attempts; last: {last_problem} ({request_ids})")

    record = {
        "inpi_id": doc.inpi_id,
        "model": message.model,
        "prompt_version": PROMPT_VERSION,
        "request_ids": request_ids,
        "attempts": len(request_ids),
        "elapsed_s": round(time.monotonic() - started, 1),
        "usage": usage,
        "extraction": parsed.model_dump(mode="json"),
    }
    path = cache_path(doc.inpi_id, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return parsed


def usage_report(model: str) -> dict:
    """Token totals and estimated cost over every cached response for `model` under the current prompt version."""
    totals = {"prompt_version": PROMPT_VERSION, "documents": 0, **dict.fromkeys(USAGE_KEYS, 0)}
    for path in sorted((config.CACHE_DIR / model / PROMPT_VERSION).glob("*.json")):
        usage = json.loads(path.read_text(encoding="utf-8"))["usage"]
        totals["documents"] += 1
        for key in USAGE_KEYS:
            totals[key] += usage.get(key) or 0
    price_in, price_out = PRICES.get(model, (float("nan"), float("nan")))
    totals["estimated_usd"] = round(
        (
            totals["input_tokens"] * price_in
            + totals["cache_creation_input_tokens"] * price_in * 1.25
            + totals["cache_read_input_tokens"] * price_in * 0.1
            + totals["output_tokens"] * price_out
        )
        / 1e6,
        4,
    )
    return totals
