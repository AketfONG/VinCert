"""PDF text extraction and page rendering via PyMuPDF."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image


def extract_page_text(pdf_path: str | Path, page_index: int = 0) -> str:
    """Return embedded text for one page (0-based)."""
    path = Path(pdf_path)
    with fitz.open(path) as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise IndexError(f"page {page_index} out of range (0..{doc.page_count - 1})")
        return doc[page_index].get_text("text")


def extract_all_text(pdf_path: str | Path, max_pages: int | None = None) -> tuple[str, int]:
    """Return concatenated text and page count."""
    path = Path(pdf_path)
    with fitz.open(path) as doc:
        n = doc.page_count
        limit = n if max_pages is None else min(n, max_pages)
        parts = [doc[i].get_text("text") for i in range(limit)]
        return "\n".join(parts), n


def page_count(pdf_path: str | Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def render_page(
    pdf_path: str | Path,
    page_index: int = 0,
    *,
    zoom: float = 2.0,
    max_width: int | None = 900,
) -> Image.Image:
    """Render a PDF page to a PIL RGB image for UI preview."""
    with fitz.open(pdf_path) as doc:
        if page_index < 0 or page_index >= doc.page_count:
            raise IndexError(f"page {page_index} out of range (0..{doc.page_count - 1})")
        page = doc[page_index]
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    if max_width and image.width > max_width:
        ratio = max_width / image.width
        image = image.resize(
            (max_width, max(1, int(image.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    return image
