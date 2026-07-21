"""High-level certificate parse pipeline."""

from __future__ import annotations

from pathlib import Path

from .models import CertificateFields, ParseResult
from .parse_metrology import parse_fields
from .pdf_io import extract_all_text, extract_page_text, page_count


def parse_certificate(
    pdf_path: str | Path,
    *,
    cover_page_only: bool = True,
) -> ParseResult:
    """
    Parse a metrology certificate PDF.

    Prefer embedded digital text (accurate for issuers like 天溯).
    cover_page_only=True uses page 0 for fields (title page), but still
    reports full page_count.
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

    if not raw.strip():
        errors.append("no embedded text on cover page (scanned PDF — OCR fallback not enabled yet)")

    fields = parse_fields(raw) if raw.strip() else CertificateFields()
    from .parse_metrology import clean_lines

    return ParseResult(
        source_path=str(path),
        page_count=n,
        raw_text=raw,
        lines=clean_lines(raw),
        fields=fields,
        method="embedded_text",
        errors=errors,
    )
