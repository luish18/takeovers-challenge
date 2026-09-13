"""Draw every event's source box on its PDF page (review/<event_id>.png) plus an index page, for checking boxes by eye."""

from __future__ import annotations

import html
import json

import pymupdf

from . import config, corpus

TARGET_WIDTH_PX = 1100


def main() -> int:
    results = json.loads(config.RESULTS_PATH.read_text(encoding="utf-8"))
    docs = {d.inpi_id: d for d in corpus.load_subject_corpus()}
    config.REVIEW_DIR.mkdir(exist_ok=True)
    rows = []
    for event in results["events"]:
        src = event["source"]
        doc = docs[src["inpi_id"]]
        with pymupdf.open(doc.pdf_path) as pdf:
            page = pdf[src["page"] - 1]
            r = page.rect
            x0, y0, x1, y1 = src["bbox"]
            page.draw_rect(pymupdf.Rect(x0 * r.width, y0 * r.height, x1 * r.width, y1 * r.height), color=(0.85, 0.1, 0.1), width=max(1.5, r.width / 400))
            zoom = TARGET_WIDTH_PX / r.width
            page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(config.REVIEW_DIR / f"{event['event_id']}.png")
        payload = {k: v for k, v in event["payload"].items() if k not in ("corroborated_by", "gloss_en", "date_basis")}
        rows.append(
            f"<section><h2>{event['event_id']} · {event['event_date']} · {event['event_code']}</h2>"
            f"<p>{html.escape(event['payload'].get('gloss_en', ''))}</p>"
            f"<p><code>{src['inpi_id']} p{src['page']} {src['bbox']}</code> — {html.escape(src.get('snippet', ''))}</p>"
            f"<pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=1))}</pre>"
            f"<img src='{event['event_id']}.png' width='{TARGET_WIDTH_PX // 2}'></section>"
        )
    (config.REVIEW_DIR / "index.html").write_text(
        "<meta charset='utf-8'><title>Event boxes</title><style>body{font-family:sans-serif;max-width:1200px;margin:auto}"
        "section{border-top:1px solid #ccc;padding:1em 0}pre{white-space:pre-wrap;font-size:12px}</style>" + "".join(rows),
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} images and {config.REVIEW_DIR / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
