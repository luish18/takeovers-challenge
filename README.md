# ARCHEAN TECHNOLOGIES (480489707) — who owned it, from its filings

Submission for the 2026-2 Takeovers challenge, *Actes* track. `results.json` (root) holds the capital and shareholder
movements and the cap table after each of them. Every value is grounded in a filed document, page and box.

## The answer in one table

| as of | capital € | shares | holders | from |
|---|---:|---:|---|---|
| 2004-12-15 | 37,000 | 370 × 100 € | AUMONT 155 · BLANCO MARINA 155 · GICQUEL 60 | incorporation statutes |
| 2005-05-17 | 150,000 | 1,500 | founders 370 + **1,130 unidentified** (GUELLATI, LEROUX, ROUJEAN among them) | EGM completes the March 2005 increase |
| 2005-08-16 | 150,000 | 1,500 | BLANCO 823 · AUMONT 617 · GICQUEL 60 | the three newcomers sell everything |
| 2006-10-20 | 150,000 | 1,500 | BLANCO **803** · AUMONT **637** · GICQUEL 60 | attendance sheet — disagrees with the line above |
| 2006-10-20 | 200,000 | 2,000 | BLANCO 953 · AUMONT 742 · CAPGRAS 225 · GICQUEL 80 | +50,000 € by debt offset; AUMONT sells 225 to CAPGRAS |
| 2007-09-07 | 200,000 | 2,000 | BLANCO 953 · HADEAN 822 · CAPGRAS 225 | AUMONT and GICQUEL contribute their shares to the new holding HADEAN |
| 2008-04-30 | 200,000 | 2,000 | HADEAN 1,047 · BLANCO 953 | CAPGRAS contributes his 225 to HADEAN |
| 2008-06-27 | 200,000 | 200,000 × 1 € | HADEAN 100 % | HADEAN is sole shareholder; par value ÷ 100. **How BLANCO's 953 shares got there is not filed anywhere.** |
| 2008-06-27 | 368,102 | 368,102 | HADEAN 200,000 · unidentified A 17,241 · unidentified B 150,861 | preference shares A/B created; two reserved increases |
| 2017-02-21 | 217,241 | 217,241 | HADEAN 100 % | buyback and cancellation of the four funds' 150,861 shares |
| 2018-03-23 | 400,000 | 400,000 | HADEAN 100 % | +182,759 € from reserves |

No filing after 2018 changes the capital (the 2018 and 2024 filings only confirm 400,000 €).

## How to run

```bash
uv sync
uv run actes build                          # results.json from the committed model responses — no API key needed
uv run actes build --model manual           # the same pipeline fed by the hand labels instead
uv run actes eval --model claude-sonnet-5   # score a model's extraction against the hand labels
uv run actes review                         # draw every event's box on its page: open review/index.html
uv run pytest
```

Re-running the extraction itself needs `ANTHROPIC_API_KEY` (see `.env.example`):

```bash
uv run actes extract --model claude-sonnet-5 --refresh
uv run actes extract --model claude-haiku-4-5 --labelled   # the comparison run
```

The responses for both prompt versions are committed (`cache/llm/<model>/<prompt>/`). To score the first prompt through
the current pipeline: `ACTES_PROMPT_VERSION=extract_v1 uv run actes eval --model claude-sonnet-5`.

The data is read from `engineering-challenges/data` (the submodule) or, if that is empty, the nearest checkout up the
tree; `ACTES_DATA_DIR` overrides both.

## How it works

```
OCR lines + ids ─► per-document extraction (LLM or hand labels) ─► overrides ─► consolidate ─► fold ─► results.json
[p3.12 @x,y] ...     events + snapshots + issues, citing line ids      reasons    dedupe,      cap tables,
                                                                       logged     ground,      checks, derived
                                                                                  conflicts    entries/exits
```

**No translation step.** The model reads the French directly and returns structured facts, the verbatim French quote
and a one-line English gloss. Translating first would have cost the one thing grounding needs: the link between a
fact and the OCR line it came from.

**The model never writes coordinates.** Each OCR line is shown as `[p3.12 @0.51,0.444] text`: its id and its top-left
position on the page. The model cites ids. Python turns the cited lines into a box (union of their polygons, 300 dpi
→ normalized with the PDF page size, checked against `bbox_viewer.py --grep`) and fuzzy-matches the quote against
those lines. A mismatch marks the event `grounded: false` instead of inventing a box. The positions are there to read
tables: OCR splits a row's name and number onto separate lines, and without positions both models paired them wrongly.

**One call per filing, Sonnet 5.** The whole corpus is ~300k tokens, and the largest filing fits in a single call, so
there is no chunking. The model answers through a `record_extraction` tool whose schema is the Pydantic model in
`schemas.py`. Strict structured outputs refused that schema ("compiled grammar is too large"), and forcing the tool
call would turn thinking off, so the call is requested in the prompt, validated, and retried once with the validation
errors. Responses are cached in `cache/llm/` and committed, so the build is reproducible without a key.

**Each event gets a status.** A filing records a movement as *completed*, *realising an earlier decision*, *pending*,
*restated history* (the running history every updated statute repeats), an *authorisation*, or *rejected*. Only the
first three move the cap table; pending ones count only if another document shows them done. This is what stops the
2008 increases being counted seven times and the 2017 authorisation being counted as the reduction.

**Consolidation** clusters the mentions of one movement across documents (same code and capital after; same parties
and shares; same class letters), keeps the operative decision as the source, lists the rest as `corroborated_by`, and
records every field on which they disagree.

**The fold** applies movements in date order and checks each state against what the documents say the cap table *is*
("snapshots": statute article 7, répartition tables, attendance sheets, "associée unique"):

- New shares whose subscriber is not named go to an explicit *Unidentified holders* bucket rather than being guessed.
- A complete holder list that disagrees with the reconstruction replaces it, and the disagreement is written into the
  state's `checks`. When the list only names who held the unidentified shares, it is a `note`, not a disagreement.
- A snapshot taken as a meeting opens applies to the state *before* that meeting's decisions.
- A share count that changes with the capital unchanged is a par-value change; holdings are rescaled.
- Mentions with nothing to apply (a capital change with no figures, a transfer with no seller or count, a restated
  incorporation capital) are skipped, listed in the notes, and not exported as events.
- Invariants are checked on every state: capital = shares × nominal, holdings add up to the share count, no negative
  share count.

**Transfers vs. entries.** Transfers and capital increases move shares. Entries and exits come from the documents when
they state them, and are otherwise derived by diffing consecutive states (`payload.derived: true`, with the mechanism's
event ids), so a movement seen from both sides is applied once. One transfer is derived with no mechanism at all —
BLANCO → HADEAN — because a holder vanished while exactly one other gained the same stake. It is flagged as
undocumented and dated at the latest possible date.

**Overrides** (`overrides.yaml`) hold the human calls: two name aliases and three event rulings, each with a reason,
and the log of what they matched is printed into `results.json` notes.
1. The EGM of 20 October 2006 only *authorises* AUMONT to sell 225 shares to CAPGRAS; later filings prove the sale
   happened, so it is promoted.
2. CAPGRAS's approval as a shareholder is promoted the same way.
3. The auditor's report on CAPGRAS's *proposed* contribution to HADEAN is demoted to pending, so that HADEAN's minutes
   of 30 April 2008 date the contribution. This third rule was added **after** scoring prompt v2.

**Beyond ARCHEAN's own folder.** ARCHEAN's filings show HADEAN as sole shareholder from June 2008 but never the
transfers that got it there. HADEAN (499979540) is one of the other nineteen companies, and three of its actes record
them: its incorporation by contribution of ARCHEAN shares (2007), the auditor's report, and CAPGRAS's contribution
(2008). They are extracted with the same prompt, pointed at ARCHEAN as the subject company
(`config.CROSS_REFERENCE_DOCS`).

## Where the documents disagree

1. **August 2005 vs. October 2006.** The EGM of 8 August 2005 records BLANCO 823 / AUMONT 617 after the transfers,
   saying this split "prevails over the one in the protocol, which contains an error". The attendance sheet of 20
   October 2006 says BLANCO 803 / AUMONT 637, with no transfer in between. The later filings side with the
   attendance sheet: in 2007 AUMONT contributes 742 shares to HADEAN, which is 637 + 330 − 225, not 617 + 330 − 225.
   The timeline follows the latest complete list and records the conflict as a check on 2006-10-20.
2. **The same August 2005 table** prints GICQUEL's 60 shares as 6,00 % (it is 4 %); the column adds to 102 %.
   Confirmed on the page image, not an OCR error.
3. **March 2018.** The decision creates 182,759 shares (+182,759 €, and 217,241 + 182,759 = 400,000). The clause
   the same decision adds to article 6, repeated in the updated statutes, says +185,759 €. Confirmed on the page
   image; the decision is used and the conflict is listed.
4. **HADEAN's statutes (2007)** say GICQUEL's shares come partly from the May 2005 increase; ARCHEAN's own records
   keep him at 60 shares from incorporation until October 2006.

## Model choice: Sonnet 5 vs. Haiku 4.5

Each run is scored against the hand labels (`uv run actes eval`, reports in `eval/`) after going through the same
overrides, consolidation and fold. So differences come from extraction alone. *Events* compares the exported events,
derived ones included. *Identical states* counts cap-table states with the same date, capital, share count and holders
as the hand-label timeline, out of 12. Haiku was only run on the 11 filings that have labels.

| model | prompt | filings | event recall | event precision | identical states | failed filings | cost |
|---|---|---:|---:|---:|---:|---:|---:|
| Sonnet 5 | v1 | 20 | 0.86 | 1.00 | 10 / 12 | 0 | $2.05 |
| **Sonnet 5** | **v2** | 20 | **1.00** | **1.00** | **12 / 12** | 0 | $2.24 |
| Haiku 4.5 | v1 | 11 | 0.83 | 0.73 | 3 / 12 | 0 | $0.43 |
| Haiku 4.5 | v2 | 11 | 0.72 | 0.88 | 5 / 12 | 1 | $0.42 |

**Prompt v2** adds line positions and five rules, each written from a v1 error:
- pair table cells by position;
- incorporation capital is not a capital increase;
- a par-value change is not a capital event;
- emit exits for the holders named in a buyback;
- report only holder lists the document prints.

**Haiku is out.** Its recall looks acceptable because consolidation and the fold rescue a lot, but its errors break
a cap table rather than miss a line:
- It makes up "complete" holder lists that no document prints (AUMONT 967 in 2006; HADEAN holding all 368,102 shares
  in 2008, which erases the investors).
- It turns three sales into nine seller-by-buyer transfers.
- It dates events from bank certificates and auditors' reports.
- With v2, it failed to produce a valid extraction for the longest filing (the 2008 preference-share decisions) even
  after a corrective retry.

About $1.80 per full run is not worth that.

**`results.json` is built from Sonnet 5 with prompt v2.** Take its 12/12 with two caveats:
- The v2 rules and the third override were written *after* seeing where earlier runs disagreed with the labels, on
  the same 11 filings. There is no held-out set, so the score is optimistic.
- The labels are themselves an AI reading (see below), so agreement means two careful readings coincide, not that
  both are right.

The remaining differences from the labels are cosmetic:
- CAPGRAS's entry has no share count.
- HADEAN's entry cluster carries a spurious date conflict from the auditor's report.
- The box on the derived BLANCO → HADEAN transfer covers half a page, because Sonnet cited two passages for that
  snapshot.

## What I could not resolve

- **Who subscribed in May 2005.** The EGM of 4 March 2005 that decided the 1,130-share increase is not in the corpus.
  The August 2005 exits of GUELLATI, LEROUX and ROUJEAN show they held shares, but not how many each.
- **How BLANCO left.** No filing records his 953 shares moving to HADEAN between October 2006 and June 2008. The
  derived transfer says so.
- **Who subscribed in June 2008.** The filed extract omits resolutions 7, 10, 12–15, 17 and 18, which name the
  beneficiaries of the A and B issues. The four funds bought out in 2017 hold exactly the B-share total, and HADEAN
  holds all 217,241 A shares afterwards, but the timeline keeps them *unidentified* from 2008 to 2017 rather than
  back-filling a guess.
- **Exact date of the AUMONT → CAPGRAS sale.** Authorised on 20 October 2006; completed before 7 September 2007. The
  authorisation date is used.
- **Effective dates of the 2006 and 2008 increases.** Both had subscription windows (to 16 November 2006, to 18 July
  2008). The decision date is used, because the updated statutes filed with the decisions already show the new capital.

## What I would do next

1. Read the missing 2005 and 2008 resolutions from INPI or the BODACC, to name the unidentified holders.
2. Back-fill unidentified buckets when a later document identifies holders of exactly that many shares of that
   class, with an explicit `inferred` flag.
3. Build a held-out check: label a second company's filings before running the prompt on them, to get an honest
   score for v2.
4. The bonus group: HADEAN owns ARCHEAN from 2008. AIR SYSTEM SERVICE (352890354, also in `data/`) subscribed cash in
   HADEAN in 2008, and ARCHEAN LABS (843071218) mentions ARCHEAN in its filings. The same extractor pointed at HADEAN
   as subject would give the next hop.
5. Tighten boxes for snapshots that cite several passages (one box per passage instead of their union).

## How I used AI

> **Author: review and rewrite this section in your own words before submitting.** It records what the AI did in
> the session that built this repository; only you can say what you checked yourself.

**Delegated.** The repository was built in one working session with Claude Code (Claude Opus 5): the plan
(`docs/PLAN.md`), all of the code and tests, both extraction prompts, running and scoring the extractions, the hand
labels in `labels/manual.yaml`, the overrides and this README. At runtime, the pipeline itself uses Claude Sonnet 5 to
read the filings, with Claude Haiku 4.5 as the comparison. Total API spend was about $5.

**Checked, and how.**
- The box conversion is unit-tested against the output of the provided `bbox_viewer.py --grep`.
- Every disagreement listed above was checked on the rendered PDF page, not only in the OCR (page crops were rendered
  and read). The two OCR tables whose pairing of names and numbers was ambiguous (the 2004 founders, the 2017 buyback)
  were checked the same way.
- `uv run actes review` draws every event's box on its page, for eyeballing. The derived BLANCO → HADEAN transfer,
  the 2017 exits and the founders' entries were inspected this way.
- **The hand labels are not an independent human gold set.** Claude Code wrote them by reading the OCR, checking the
  ambiguous tables on page images. Scores in the model comparison therefore measure agreement with a careful AI
  reading, not with ground truth.

**Where the AI got it wrong.**
- **Cost estimate.** The plan estimated $0.50–1 for a full Sonnet run; each full run cost just over $2. The plan forgot
  thinking tokens and the three HADEAN filings.
- **Schema.** The first extraction design used strict structured outputs; the API rejected the schema as too large to
  compile. It became a validated tool call.
- **Invented transfer.** The first version of the fold invented an 823-share BLANCO → AUMONT transfer out of a
  20-share disagreement between two holder lists. It was caught while reading the derived events, fixed (derived
  transfers now require the seller to leave), and pinned by a test.
- **Pipeline bugs found by scoring.** Scoring the models against the labels exposed four: a figure-less "increase"
  mention wiped the share count, a transfer of unknown size erased a known holding, share-class names were matched
  too loosely, and a par-value split coded as a decrease drove the share count negative. These fixes alone moved
  Sonnet's v1 timeline from 7 to 10 identical states out of 12, and they are pinned by tests.
- **Extraction errors.** Both models turned the incorporation capital into a "capital increase" dated from the bank
  certificate. Haiku made up complete holder lists that no document prints. Sonnet mis-paired the founders' table,
  where OCR splits names and numbers onto separate lines, and would not emit exits for bought-back holders. Each
  became a rule or an input change in prompt v2, which is why the v2 score is optimistic (see above).
