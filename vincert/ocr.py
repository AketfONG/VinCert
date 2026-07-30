"""PaddleOCR cover-page recognition (Windows-oriented, no Django).

Adapted from 证书扫描/ji_liang ``pdf_to_text_tools.myppocr``:
PDF cover → image → Chinese OCR text lines.

Runs OCR in-memory (PIL → numpy) to avoid OpenCV Unicode path issues
on Chinese Windows paths. The engine is loaded once and reused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

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
    """Lazy singleton PaddleOCR (Chinese, no angle classifier)."""
    global _ocr_engine
    _ensure_imports()
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        # Mirror ji_liang: Chinese certs, cls off for speed/stability.
        _ocr_engine = PaddleOCR(
            use_angle_cls=False,
            lang="ch",
            show_log=False,
            use_gpu=False,
        )
    return _ocr_engine


def ocr_image_lines(image: Image.Image) -> list[str]:
    """Run Chinese OCR on a PIL image; return text lines in reading order."""
    import numpy as np

    engine = get_ocr_engine()
    rgb = image.convert("RGB")
    # PaddleOCR accepts ndarray; keep RGB (engine handles layout).
    arr = np.asarray(rgb)
    result = engine.ocr(arr, cls=False)
    lines: list[str] = []
    if not result:
        return lines
    for block in result:
        if not block:
            continue
        for item in block:
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
    zoom: float = 1.5,
) -> list[str]:
    """
    OCR the cover page (page 0) of a PDF.

    ``zoom=1.5`` roughly matches ji_liang's ``covert2pic(..., zoom=150)``
    (150% scale) without writing temp PNGs to disk.
    """
    from .pdf_io import render_page

    image = render_page(pdf_path, 0, zoom=zoom, max_width=None)
    return ocr_image_lines(image)
