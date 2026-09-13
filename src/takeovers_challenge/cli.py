"""`uv run actes extract | build | eval | usage`."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config


def cmd_extract(args: argparse.Namespace) -> int:
    import anthropic

    from . import corpus, extract, manual

    docs = corpus.load_subject_corpus()
    if args.doc:
        docs = [d for d in docs if any(d.inpi_id.endswith(suffix) for suffix in args.doc)]
    if args.labelled:
        docs = [d for d in docs if (label := manual.load(d)) is not None and (label.events or label.snapshots)]
    client = anthropic.Anthropic()

    def run(doc):
        cached = not args.refresh and extract.load_cached(doc.inpi_id, args.model) is not None
        return cached, extract.extract(doc, args.model, client=client, refresh=args.refresh)

    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, doc): doc for doc in docs}
        for future in as_completed(futures):
            doc = futures[future]
            try:
                cached, result = future.result()
            except (extract.ExtractionError, anthropic.APIError) as err:
                failures += 1
                print(f"FAILED {doc.deposit_date} {doc.inpi_id}: {err}", file=sys.stderr)
                continue
            print(
                f"{'cached' if cached else 'fresh '} {doc.deposit_date} {doc.inpi_id}  "
                f"events={len(result.events):2d} snapshots={len(result.snapshots):2d} issues={len(result.issues)}",
                flush=True,
            )
    print(json.dumps(extract.usage_report(args.model), indent=2))
    return 1 if failures else 0


def cmd_build(args: argparse.Namespace) -> int:
    from . import export

    return export.main(model=args.model)


def cmd_eval(args: argparse.Namespace) -> int:
    from . import eval as evaluation

    report = evaluation.run(args.model)
    ev, tl = report["events"], report["timeline"]
    print(f"events: recall {ev['recall']} precision {ev['precision']} ({ev['matched']}/{ev['gold']} gold, {ev['model']} model)")
    print(f"grounding: {ev['grounded_rate']} grounded, same page {ev['same_source_page_rate']}, IoU {ev['mean_bbox_iou_on_same_page']}")
    print(f"timeline: {tl['exact_matches']}/{tl['gold_states']} states identical ({tl['model_states']} model states)")
    for key in ("missed", "extra", "date_mismatches", "value_mismatches"):
        for item in ev[key]:
            print(f"  {key}: {item}")
    for item in tl["diffs"] + tl["extra_states"]:
        print(f"  timeline: {item}")
    print(json.dumps(report["usage"]))
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    from . import extract

    print(json.dumps(extract.usage_report(args.model), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="actes")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="run (or load cached) per-document extraction")
    p.add_argument("--model", default=config.EXTRACT_MODEL)
    p.add_argument("--doc", nargs="*", help="inpi_id suffixes to restrict to")
    p.add_argument("--labelled", action="store_true", help="only documents with hand labels (for model comparisons)")
    p.add_argument("--refresh", action="store_true", help="ignore the cache")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("build", help="consolidate extractions into results.json (--model manual for the hand labels)")
    p.add_argument("--model", default=config.EXTRACT_MODEL)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("eval", help="score a model's extractions against the hand labels")
    p.add_argument("--model", default=config.EXTRACT_MODEL)
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("review", help="draw each event's box on its page into review/ (open review/index.html)")
    p.set_defaults(func=lambda args: __import__("takeovers_challenge.review", fromlist=["main"]).main())

    p = sub.add_parser("usage", help="token and cost totals from the cache")
    p.add_argument("--model", default=config.EXTRACT_MODEL)
    p.set_defaults(func=cmd_usage)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
