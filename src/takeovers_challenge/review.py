"""Draw every event's source box on its PDF page, into one self-contained review/index.html for checking boxes by eye.

Each image is a JPEG crop of the page around the box, embedded as a data URI, so the page shows its images wherever it
is opened (a browser, an app preview, a copy sent to someone) instead of depending on sibling image files.
"""

from __future__ import annotations

import base64
import html
import json

import pymupdf

from . import config, corpus

CROP_WIDTH_PX = 900
CONTEXT = 0.06  # fraction of the page height shown above and below the box
JPEG_QUALITY = 70


def crop_jpeg(pdf_path, page_number: int, bbox: list[float]) -> bytes:
    with pymupdf.open(pdf_path) as pdf:
        page = pdf[page_number - 1]
        r = page.rect
        x0, y0, x1, y1 = bbox
        page.draw_rect(
            pymupdf.Rect(x0 * r.width, y0 * r.height, x1 * r.width, y1 * r.height),
            color=(0.85, 0.1, 0.1),
            width=max(1.5, r.width / 400),
        )
        clip = pymupdf.Rect(0, max(0.0, y0 - CONTEXT) * r.height, r.width, min(1.0, y1 + CONTEXT) * r.height)
        zoom = CROP_WIDTH_PX / r.width
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).tobytes("jpeg", jpg_quality=JPEG_QUALITY)


def main() -> int:
    results = json.loads(config.RESULTS_PATH.read_text(encoding="utf-8"))
    docs = {d.inpi_id: d for d in corpus.load_subject_corpus()}
    config.REVIEW_DIR.mkdir(exist_ok=True)
    for stale in config.REVIEW_DIR.glob("*.png"):  # images from earlier versions of this page
        stale.unlink()

    rows = []
    for event in results["events"]:
        src = event["source"]
        doc = docs[src["inpi_id"]]
        image = base64.b64encode(crop_jpeg(doc.pdf_path, src["page"], src["bbox"])).decode("ascii")
        payload = {k: v for k, v in event["payload"].items() if k not in ("corroborated_by", "gloss_en", "date_basis")}
        rows.append(
            f"<section><h2>{event['event_id']} · {event['event_date']} · {event['event_code']}</h2>"
            f"<p>{html.escape(event['payload'].get('gloss_en', ''))}</p>"
            f"<p><code>{src['inpi_id']} p{src['page']} {src['bbox']}</code> — {html.escape(src.get('snippet', ''))}</p>"
            f"<pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=1))}</pre>"
            f"<img alt='page {src['page']} of {src['inpi_id']}, box in red' src='data:image/jpeg;base64,{image}'></section>"
        )
    out = config.REVIEW_DIR / "index.html"
    out.write_text(
        "<meta charset='utf-8'><title>Event boxes</title><style>body{font-family:sans-serif;max-width:1000px;margin:auto;padding:0 1em}"
        "section{border-top:1px solid #ccc;padding:1em 0}pre{white-space:pre-wrap;font-size:12px}"
        f"img{{max-width:100%;border:1px solid #ddd}}</style>" + "".join(rows),
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(rows)} events, {out.stat().st_size / 1e6:.1f} MB, images embedded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
