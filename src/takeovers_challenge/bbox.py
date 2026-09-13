"""OCR polygons (300-dpi pixels) to submittable boxes ([x0, y0, x1, y1], normalized 0-1, origin top-left)."""

from __future__ import annotations

from collections.abc import Iterable

OCR_DPI = 300
POINTS_PER_INCH = 72

Polygon = list[list[float]]
Box = list[float]


def polygon_to_norm(polygon: Polygon, page_w_pt: float, page_h_pt: float) -> Box:
    """Same convention as tools/bbox_viewer.py, clamped to the page."""
    scale = OCR_DPI / POINTS_PER_INCH
    w_px, h_px = page_w_pt * scale, page_h_pt * scale
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    box = [min(xs) / w_px, min(ys) / h_px, max(xs) / w_px, max(ys) / h_px]
    return [min(max(v, 0.0), 1.0) for v in box]


def union(boxes: Iterable[Box]) -> Box:
    boxes = list(boxes)
    if not boxes:
        raise ValueError("union of no boxes")
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def rounded(box: Box, ndigits: int = 4) -> Box:
    return [round(v, ndigits) for v in box]
