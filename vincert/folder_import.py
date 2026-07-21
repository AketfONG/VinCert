"""Discover certificate PDFs in a folder."""

from __future__ import annotations

from pathlib import Path


def find_pdfs_in_folder(folder: str | Path, *, recursive: bool = False) -> list[Path]:
    """
    Return sorted PDF paths in ``folder``.

    By default only the folder root is scanned (no subdirectories).
    """
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a folder: {root}")
    if recursive:
        return sorted(p for p in root.rglob("*.pdf") if p.is_file())
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
