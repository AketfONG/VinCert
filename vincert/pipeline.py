"""High-level certificate parse pipeline."""

from __future__ import annotations

from pathlib import Path

from .models import CertificateFields, ParseResult
from .parse_metrology import clean_lines, parse_fields
from .pdf_io import extract_all_text, extract_page_text, page_count


def parse_certificate(
    pdf_path: str | Path,
    *,
    cover_page_only: bool = True,
    use_ocr_fallback: bool = True,
) -> ParseResult:
    """
    Parse a metrology certificate PDF.

    Prefer embedded digital text (accurate for issuers like 天溯).
    If the cover page has no text and ``use_ocr_fallback`` is True, run
    PaddleOCR on a rendered cover image (same approach as 证书扫描).
    """
    path = Path(pdf_path)
    errors: list[str] = []
    if not path.exists():
        return ParseResult(
            source_path=str(path),
            page_count=0,
            raw_text="",
            lines=[],
            fields=CertificateFields(),
            method="embedded_text",
            errors=[f"file not found: {path}"],
        )

    try:
        n = page_count(path)
        if cover_page_only:
            raw = extract_page_text(path, 0)
        else:
            raw, n = extract_all_text(path)
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return ParseResult(
            source_path=str(path),
            page_count=0,
            raw_text="",
            lines=[],
            fields=CertificateFields(),
            method="embedded_text",
            errors=[str(exc)],
        )

    method = "embedded_text"
    if not raw.strip() and use_ocr_fallback:
        try:
            from .ocr import ocr_availability_message, ocr_pdf_cover, is_ocr_available

            if not is_ocr_available():
                errors.append(ocr_availability_message())
            else:
                ocr_lines = ocr_pdf_cover(path)
                raw = "\n".join(ocr_lines)
                method = "ocr"
                if not raw.strip():
                    errors.append("OCR produced no text on cover page")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"OCR failed: {exc}")
    elif not raw.strip():
        errors.append("no embedded text on cover page (OCR fallback disabled)")

    fields = parse_fields(raw) if raw.strip() else CertificateFields()

    return ParseResult(
        source_path=str(path),
        page_count=n,
        raw_text=raw,
        lines=clean_lines(raw) if raw.strip() else [],
        fields=fields,
        method=method,
        errors=errors,
    )
