"""High-accuracy certificate OCR, aligned with 证书扫描/ji_liang.

Pipeline (same steps as ``pdf_to_text_tools``):
  1. Render cover page at zoom=150 (``covert2pic``)
  2. Run Chinese PaddleOCR with ``cls=False`` (``myppocr``)
  3. Return text lines in reading order for field extraction

Runs in-memory (PIL → numpy) to avoid OpenCV Unicode path issues on
Chinese Windows paths. The engine is loaded once and reused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

# ji_liang ``covert2pic(pdf, 150, ...)`` — larger = sharper, slower.
OCR_ZOOM_PERCENT = 150

_ocr_engine: Any | None = None
_ocr_import_error: str | None = None


def is_ocr_available() -> bool:
    """Return True if PaddleOCR can be imported on this machine."""
    try:
        _ensure_imports()
        return True
    except Exception:  # noqa: BLE001
        return False


def ocr_availability_message() -> str:
    """Human-readable reason when OCR is unavailable."""
    if is_ocr_available():
        return ""
    if _ocr_import_error:
        return _ocr_import_error
    return (
        "PaddleOCR is not installed. On Windows (Python 3.11, 64-bit) run:\n"
        "  pip install paddlepaddle==2.6.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/\n"
        "  pip install paddleocr==2.7.3 opencv-python numpy"
    )


def _ensure_imports() -> None:
    global _ocr_import_error
    try:
        import numpy  # noqa: F401
        import paddle  # noqa: F401
        from paddleocr import PaddleOCR  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        _ocr_import_error = (
            f"OCR dependencies missing ({exc}). "
            "Install Windows CPU PaddlePaddle + PaddleOCR (see requirements.txt)."
        )
        raise


def get_ocr_engine() -> Any:
    """Lazy singleton PaddleOCR — mirrors ji_liang ``PaddleOCR(lang='ch')``."""
    global _ocr_engine
    _ensure_imports()
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(
            use_angle_cls=False,
            lang="ch",
            show_log=False,
            use_gpu=False,
        )
    return _ocr_engine


def render_cover_for_ocr(
    pdf_path: str | Path,
    *,
    zoom_percent: int = OCR_ZOOM_PERCENT,
) -> Image.Image:
    """Render PDF cover like ji_liang ``covert2pic`` (fitz Matrix zoom/100)."""
    import fitz

    path = Path(pdf_path)
    zoom = max(int(zoom_percent), 50) / 100.0
    with fitz.open(path) as doc:
        if doc.page_count < 1:
            raise ValueError(f"empty PDF: {path}")
        page = doc[0]
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _box_sort_key(item: Any) -> tuple[float, float]:
    """Reading order: top-to-bottom, then left-to-right."""
    try:
        box = item[0]
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        return (min(ys), min(xs))
    except Exception:  # noqa: BLE001
        return (0.0, 0.0)


def ocr_image_lines(image: Image.Image) -> list[str]:
    """Run Chinese OCR on a PIL image; return text lines in reading order."""
    import numpy as np

    engine = get_ocr_engine()
    rgb = image.convert("RGB")
    arr = np.asarray(rgb)
    # ji_liang: ``ocr.ocr(..., cls=False)``
    result = engine.ocr(arr, cls=False)
    lines: list[str] = []
    if not result:
        return lines

    items: list[Any] = []
    for block in result:
        if not block:
            continue
        items.extend(block)
    items.sort(key=_box_sort_key)

    for item in items:
        try:
            text = item[1][0]
        except (IndexError, TypeError, ValueError):
            continue
        text = (text or "").strip()
        if text:
            lines.append(text)
    return lines


def ocr_pdf_cover(
    pdf_path: str | Path,
    *,
    zoom_percent: int = OCR_ZOOM_PERCENT,
) -> list[str]:
    """
    OCR the cover page only — ji_liang ``main`` uses ``listdir(...)[:1]``.

    Steps: render @ ``zoom_percent`` → PaddleOCR → text lines.
    """
    image = render_cover_for_ocr(pdf_path, zoom_percent=zoom_percent)
    return ocr_image_lines(image)
