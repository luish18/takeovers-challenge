"""Load a company's actes: registry metadata, per-page OCR lines with stable ids, and PDF page sizes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pymupdf

from . import bbox, config


@dataclass(frozen=True)
class Line:
    page: int
    index: int
    text: str
    polygon: bbox.Polygon
    score: float

    @property
    def id(self) -> str:
        """Stable id the model cites, e.g. `p3.12` = page 3, 13th OCR line."""
        return f"p{self.page}.{self.index}"


@dataclass(frozen=True)
class Page:
    number: int
    width_pt: float
    height_pt: float
    lines: list[Line]

    def norm_box(self, line: Line) -> bbox.Box:
        return bbox.polygon_to_norm(line.polygon, self.width_pt, self.height_pt)


@dataclass
class Document:
    inpi_id: str
    deposit_date: str
    meta: dict
    pdf_path: Path
    ocr_dir: Path
    pages: list[Page] = field(repr=False)

    @property
    def filing_types(self) -> list[str]:
        """The registry's own labels for what the filing contains."""
        labels = [
            " / ".join(p for p in (t.get("typeActe", "").strip(), t.get("decision", "").strip()) if p)
            for t in self.meta.get("typeRdd", [])
        ]
        if self.meta.get("libelle"):
            labels.append(self.meta["libelle"].strip())
        return [label for label in labels if label]

    @cached_property
    def _lines_by_id(self) -> dict[str, tuple[Page, Line]]:
        return {line.id: (page, line) for page in self.pages for line in page.lines}

    def lookup(self, line_id: str) -> tuple[Page, Line] | None:
        """Tolerates ids cited with their position suffix, e.g. 'p3.12 @0.51,0.444'."""
        return self._lines_by_id.get(line_id.strip().split("@")[0].strip())

    def render(self, positions: bool = False) -> str:
        """The document as the model sees it: one OCR line per row, prefixed by its id (and top-left position)."""
        out = []
        for page in self.pages:
            out.append(f"===== PAGE {page.number} =====")
            for line in page.lines:
                if positions:
                    x0, y0, _, _ = page.norm_box(line)
                    out.append(f"[{line.id} @{x0:.2f},{y0:.3f}] {line.text}")
                else:
                    out.append(f"[{line.id}] {line.text}")
        return "\n".join(out)


def load_document(meta_path: Path) -> Document:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    actes_dir = meta_path.parent.parent
    inpi_id = meta["id"]
    pdf_path = actes_dir / "pdf" / meta_path.with_suffix(".pdf").name
    ocr_dir = actes_dir / "ocr" / inpi_id

    pages = []
    with pymupdf.open(pdf_path) as pdf:
        for page_json in sorted(ocr_dir.glob("page_*.json")):
            raw = json.loads(page_json.read_text(encoding="utf-8"))
            number = raw["page"]
            rect = pdf[number - 1].rect
            lines = [
                Line(number, i, (item.get("text") or "").strip(), item["polygon"], item.get("score", 0.0))
                for i, item in enumerate(raw["ocr"])
            ]
            pages.append(Page(number, rect.width, rect.height, lines))

    return Document(inpi_id, meta["dateDepot"], meta, pdf_path, ocr_dir, pages)


def load_documents(siren: str = config.SIREN) -> list[Document]:
    meta_dir = config.DATA_DIR / siren / "actes" / "meta"
    docs = [load_document(p) for p in sorted(meta_dir.glob("*.json"))]
    return sorted(docs, key=lambda d: (d.deposit_date, d.inpi_id))


def load_subject_corpus() -> list[Document]:
    """The subject's own actes plus the cross-referenced filings of other companies (config.CROSS_REFERENCE_DOCS)."""
    docs = load_documents()
    for siren, ids in config.CROSS_REFERENCE_DOCS.items():
        meta_dir = config.DATA_DIR / siren / "actes" / "meta"
        docs += [load_document(next(meta_dir.glob(f"*_{inpi_id}.json"))) for inpi_id in ids]
    return sorted(docs, key=lambda d: (d.deposit_date, d.inpi_id))
