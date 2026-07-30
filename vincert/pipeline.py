"""High-level certificate parse pipeline."""

from __future__ import annotations

from dataclasses import asdict, fields as dataclass_fields
from pathlib import Path

from .models import CertificateFields, ParseResult
from .parse_metrology import clean_lines, parse_fields
from .pdf_io import extract_page_text, page_count

# Fill these from later pages when the cover left them blank.
_FILL_KEYS = (
    "name",
    "serial_num",
    "model",
    "measurement_unit",
    "measurement_date",
    "measurement_type",
    "certificate_no",
    "client_name",
    "manufacturer",
    "due_date",
    "issue_date",
)

# Obvious bilingual table headers that must never become field values.
_REJECT_VALUES = {
    "model",
    "name",
    "number",
    "description",
    "manufacturer",
    "customer",
    "address",
    "page",
    "of",
}


def _is_usable_value(key: str, value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if text.lower() in _REJECT_VALUES:
        return False
    if key == "model" and text.isascii() and text.isalpha() and len(text) <= 12:
        # e.g. English header "Model" slipped through
        return False
    return True


def _missing_fill_keys(fields: CertificateFields) -> list[str]:
    return [key for key in _FILL_KEYS if not getattr(fields, key, "").strip()]


def merge_fields(
    primary: CertificateFields,
    secondary: CertificateFields,
) -> CertificateFields:
    """Keep cover values; fill only blank fields from another page's parse."""
    merged = CertificateFields(**asdict(primary))
    for key in _FILL_KEYS:
        if getattr(merged, key, "").strip():
            continue
        candidate = getattr(secondary, key, "")
        if _is_usable_value(key, candidate):
            setattr(merged, key, candidate.strip())
    return merged


def parse_certificate(
    pdf_path: str | Path,
    *,
    cover_page_only: bool = False,
    use_ocr_fallback: bool = True,
    fill_missing_pages: bool = True,
) -> ParseResult:
    """
    Parse a metrology certificate PDF.

    Prefer embedded digital text on the cover page.
    If key fields are still blank, scan later pages and fill only missing values
    (e.g. 上海计量 puts 校准日期 on page 2).
    If the cover has no text and ``use_ocr_fallback`` is True, run PaddleOCR
    on a rendered cover image.
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
        raw = extract_page_text(path, 0)
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
    used_extra_pages = False

    should_scan_pages = (fill_missing_pages or not cover_page_only) and n > 1
    if should_scan_pages and _missing_fill_keys(fields):
        # Always scan remaining pages for blanks; cover_page_only=False also
        # keeps concatenating raw text for debugging/display.
        for page_index in range(1, n):
            if cover_page_only and not _missing_fill_keys(fields):
                break
            try:
                page_raw = extract_page_text(path, page_index)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"page {page_index + 1} read failed: {exc}")
                continue
            if not page_raw.strip():
                continue
            if not cover_page_only:
                raw = f"{raw}\n{page_raw}" if raw.strip() else page_raw
            page_fields = parse_fields(page_raw)
            before = _missing_fill_keys(fields)
            fields = merge_fields(fields, page_fields)
            if _missing_fill_keys(fields) != before:
                used_extra_pages = True
            if cover_page_only and not _missing_fill_keys(fields):
                break

    if used_extra_pages and method == "embedded_text":
        method = "embedded_text_multipage"
    elif used_extra_pages and method == "ocr":
        method = "ocr+embedded_pages"

    return ParseResult(
        source_path=str(path),
        page_count=n,
        raw_text=raw,
        lines=clean_lines(raw) if raw.strip() else [],
        fields=fields,
        method=method,
        errors=errors,
    )
