# Plan — Actes challenge: ARCHEAN TECHNOLOGIES (480489707) capital timeline

Deliverables (from `challenges/actes/BRIEF.md`): `results.json` (schema-valid, `events[]` +
`capital_timeline[]`), `README.md` (run instructions, trade-offs, **How I used AI**, unresolved,
next steps), `.env.example`. Budget: 6–8 h. Bonus (group graph) only if time remains.

---

## 1. What the data looks like (measured, not assumed)

- 17 actes, 2005 → 2025, **OCR present for every page of every PDF** (page counts match).
- Whole corpus ≈ 710 k characters of OCR ≈ 200–250 k tokens. The largest document is ≈ 80 k chars
  (≈ 25 k tokens). **Every document fits in a single model call. No chunking or page triage needed.**
- OCR quality is good (mean line score 0.94–0.99), with errors on signatures and stamps.
- The meta `typeRdd` labels already tell us where to look: constitution (2005), capital increase
  (2007, 2008-07), preference-share CAA reports (2008-06, 2008-09), capital reduction (2017-01,
  2017-03), capital increase (2018-05), plus several "Statuts mis à jour".
- **Restated statutes recur.** At least 7 documents carry a full updated copy of the statutes.
  Article 6 ("Apports") holds a running history of every capital change, and article 7
  ("Capital social") gives the current capital, the share count, the nominal value and sometimes
  the holders. Example from 2018-05-30, p.3: *+185 759 € par incorporation de réserves → 400 000 €,
  décision du 23 mars 2018; 400 000 actions de 1 €, intégralement détenues par la société HADEAN*.
  This matters in two ways:
  - It helps: each restatement is an independent snapshot we can check the timeline against.
  - It is a trap: the same historical increase will be "found" in 5+ documents, so the pipeline
    must deduplicate.
- The deposit date is not the effective date. In the example above, the decision is 2018-03-23 and
  the deposit is 2018-05-30.

## 2. Key design decisions

### 2.1 Don't translate as a separate step. Extract straight from the French.
A translate-then-classify pipeline breaks **grounding**. Every event needs a page and a bbox, and
bboxes belong to the *French* OCR lines. A translated text has no coordinates, and it adds a second
place for meaning to drift (legal French terms like *apport en nature*, *incorporation de réserves*
or *agrément* often lose their specific meaning in translation).

Instead, the model reads the French OCR directly and returns:
- structured fields in English/normalized form (codes, amounts, dates, names),
- the **OCR line ids** it relied on,
- the **verbatim French snippet**,
- a one-line **English gloss** for human review and the README.

So translation still happens, but as a field in the output, not as its own pipeline stage.

### 2.2 The LLM never produces coordinates
Each OCR line gets an id such as `[p03:L012]` in the prompt. The model cites ids, and Python
computes the bbox as the union of the cited lines' polygons, normalized using the PDF page size
(`px_per_pt = 300/72`, as in `tools/bbox_viewer.py`). We then validate that the snippet
fuzzy-matches the cited lines' text. If it doesn't, the event is marked ungrounded instead of
getting an invented box.

### 2.3 Model choice: Sonnet 5 by default, Haiku 4.5 as a measured comparison
At this corpus size, a full run costs roughly **$0.50–1.00 on `claude-sonnet-5`** ($2/$10 per MTok)
against roughly half that on `claude-haiku-4-5` ($1/$5). The saving is cents, while the task
(dates vs. deposit dates, share transfer vs. entry, restatement vs. new decision) needs judgment.
So: **Sonnet 5 is the default**. The model name comes from an env var, and on a small hand-labelled
set we run Haiku too and report the accuracy difference in the README. That gives the reviewers a
real trade-off to read, not an assumption. Optionally, a single Opus 5 "reviewer" call over the
consolidated timeline hunts for contradictions (§4 step 6).

API usage (Python `anthropic` SDK):
- `client.messages.parse(...)` with Pydantic models → schema-validated JSON, no hand parsing.
- Stable system prompt (event-code definitions + French legal glossary + output rules) with
  `cache_control`, so the 17 calls reuse the cached prefix.
- **On-disk response cache** keyed by `(doc_id, prompt_version, model)`, committed to the repo.
  Reruns are then free and deterministic, and **reviewers can rebuild `results.json` without an
  API key** (worth stating in the README).

### 2.4 Keep the LLM for reading and use Python for reasoning
The LLM extracts *facts per document*. Deduplication, ordering, cap-table arithmetic and consistency
checks are deterministic Python, because that is where "internally coherent" gets decided, and it
has to be auditable.

### 2.5 Transfer vs. entry: one movement, two views
Following `event_codes.json`, `SHAREHOLDER_SHARE_TRANSFER` / `CAPITAL_INCREASE` are the
**mechanism**, and `SHAREHOLDER_ENTRY` / `SHAREHOLDER_END` are the **consequence**. The rules:
- The timeline fold applies **only mechanisms** to share counts.
- ENTRY/END are **derived** by diffing holder sets before and after each state, then emitted as
  events linked via `payload.mechanism_event_id`.
- An explicitly stated entry with no mechanism found (e.g. the holder appears in a later statute) is
  kept as an ENTRY with `shares` from the snapshot and a note, and is never applied twice.
- The timeline entry's `caused_by` lists both the mechanism and its consequence events.

## 3. Repository layout

```
src/takeovers_challenge/
  config.py        # paths (DATA_DIR), model names, prompt version — from env
  corpus.py        # load meta + OCR + PDF page sizes → Document/Page/Line(id, text, polygon)
  bbox.py          # polygon(s) → normalized [x0,y0,x1,y1]; union of lines
  schemas.py       # Pydantic: ExtractedEvent, CapitalSnapshot, DocExtraction
  prompts/extract_v1.md
  extract.py       # per-document LLM call + disk cache
  consolidate.py   # grounding validation, dedupe across docs, conflict detection
  timeline.py      # chronological fold, ENTRY/END derivation, invariants
  export.py        # results.json assembly + jsonschema validation
  cli.py           # `uv run actes extract|build|check`
tests/
  test_bbox.py     # matches bbox_viewer --grep output on known lines
  test_timeline.py # fold arithmetic, transfer/entry non-double-counting
cache/llm/         # committed JSON responses
overrides.yaml     # manual adjudications, each with a reason + source
results.json  README.md  .env.example
```

`.env.example`: `ANTHROPIC_API_KEY=`, `ACTES_EXTRACT_MODEL=`, `ACTES_REVIEW_MODEL=`, `ACTES_DATA_DIR=`.

Dependencies to add: `anthropic`, `pydantic`, `pymupdf`, `pillow`, `jsonschema`, `python-dotenv`,
`pytest`. The existing `pandas`/`tqdm` can stay or go. **Risk:** the project pins Python 3.14, so
check early that `pymupdf` wheels install. If they don't, drop to 3.12/3.13.

Note: the `engineering-challenges` submodule is not initialized in this worktree. Run
`git submodule update --init`, or point `ACTES_DATA_DIR` at the main checkout.

## 4. Pipeline, step by step

**Step 0: Setup (≈20 min).** Dependencies, `.env.example`, `.gitignore` for `.env`, config.

**Step 1: Corpus + bbox (≈45 min).** Load meta/OCR/page sizes, assign line ids, implement the bbox
conversion. Unit test it against `bbox_viewer.py --grep "capital social"` on 2018-05-30 p.3.
*Done when* our boxes equal the tool's to 4 decimals.

**Step 2: Prompt + schema (≈45 min).** For each document the model returns:
- `decision_dates[]`: the effective date(s) of the decisions in this act, with evidence lines.
- `events[]`: `code`, `effective_date`, `payload` (per `event_codes.json`), `evidence_line_ids`,
  `snippet_fr`, `gloss_en`, `is_restatement` (describes a *past* change recalled in statutes vs.
  a decision taken *in this act*), `confidence`, `notes`.
- `capital_snapshots[]`: `as_of` (or "as of this document"), `capital_eur`, `shares_total`,
  `nominal_eur`, `holders[]` (name, kind, shares, siren if printed), evidence lines.
- `out_of_scope_seen`: brief list (president, auditor…), used only for a sanity check.

The glossary in the system prompt covers: *augmentation / réduction du capital, apport en numéraire
/ en nature, incorporation de réserves, compensation de créances, BSA/BSPCE, cession / cédant /
cessionnaire, agrément, associé unique, actions de préférence, rachat et annulation, valeur
nominale, regroupement, fusion / TUP*.

**Step 3: Gold mini-set (≈40 min, by hand).** Label 3–4 documents manually: 2018-05-30 (increase by
reserves), 2017-01-20 (reduction, 4 pages), 2008-07-15 (increase + preference shares), 2005
constitution (initial state). This is the eval for prompt iteration and the Sonnet vs. Haiku
comparison. It is small, but it is honest about being small.

**Step 4: Extraction run (≈45 min including iteration).** Run all 17 documents on Sonnet 5 and the
gold docs on Haiku 4.5. Iterate the prompt against the gold set 1–2 times at most. Record usage and
cost.

**Step 5: Consolidation + timeline (≈2 h, the core).**
1. Grounding check: line ids exist, the snippet matches, the bbox is computed. Otherwise the event
   is flagged `grounded: false` with a note.
2. Dedupe: the key is `(code, effective_date, capital_after / amount, parties)`. Prefer the
   **original decision document** as `source`, and attach the restatements as
   `payload.corroborated_by[]`.
3. Conflicts: the same event with different amounts or dates across documents → keep the best
   supported version, and list the disagreement in `notes`. The brief says the capital chain
   contains contradictions, so finding them is part of the score.
4. Fold in date order, starting from the constitution snapshot. After each event check:
   `capital == shares × nominal`, `prev_capital ± amount == capital_after`, and
   `Σ holder shares == shares_total` (when holders are known).
5. Reconcile every statute snapshot against the folded state at that date. Mismatches go to
   `notes`.
6. Unknown holders: SAS statutes usually don't list shareholders. Model the gap explicitly as a
   holder `"Unidentified holders"` (`kind: UNKNOWN`, `shares` = remainder or null) rather than
   pretending the table is complete.
7. `overrides.yaml` for human adjudications: each one carries a reason and a source, and is applied
   deterministically. It is listed in the README, so any hand-made call is visible and not hidden
   in prompts.
8. *(Optional)* one Opus 5 review call over the consolidated events + snapshots: "list
   inconsistencies". Its findings are verified by hand before entering `notes`.

**Step 6: Export + verification (≈45 min).** Assemble `results.json`, validate it against
`results.schema.json`, render a `bbox_viewer.py --bbox` PNG for every event into
`review/` (gitignored), and eyeball each one. There are few enough events that checking all of
them is feasible.

**Step 7: README (≈45 min).** How to run (with and without a key), the architecture from §2 and
why, the Sonnet vs. Haiku result, **How I used AI** (what was delegated, what was checked, where
the tool was wrong), the contradictions found, what was unresolved, and next steps.

Total ≈ 7 h. **If time runs short, cut in this order:** the Opus review call → the Haiku comparison
→ derived ENTRY/END polish. **Never cut** grounding validation, the invariant checks, or the honest
README.

## 5. Bonus (only after the timeline is done)
HADEAN appears as sole shareholder in 2018. Cheap first move: scan the `meta/*.json` `denomination`
of the other 19 folders for names found in ARCHEAN's holders, then run the same extractor on the
matching company's actes/bilans to go one hop up. Anything that doesn't match gets
`resolved: false`.

## 6. Known risks
- Model confuses **restated history** with **new decisions** → `is_restatement` flag + dedupe +
  gold set.
- Effective date vs. deposit date → the prompt requires a cited date line; a missing date falls
  back to the deposit date with a note.
- OCR digit errors in amounts (`185 759` vs `185 739`) → the arithmetic invariants catch them, and
  cross-document corroboration breaks the tie.
- Holder identity (e.g. "société HADEAN" vs. "HADEAN SAS") → name normalization plus a small alias
  map in `overrides.yaml`.
