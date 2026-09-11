"""Deliverable text extraction for judging.

Judges compare text renderings of office documents. Extraction is best
effort and symmetric — gold and worker submissions go through exactly the
same path, so extraction artefacts cannot systematically favour a side.
"""

from __future__ import annotations

import os

import openpyxl

from ..textio import docx_text

_MAX_CHARS_PER_FILE = 30_000
_MAX_ROWS = 300


def _xlsx_text(path: str) -> str:
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return "(workbook unreadable)"
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"# Sheet: {ws.title}")
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if i > _MAX_ROWS:
                lines.append("# (sheet truncated)")
                break
            cells = [str(v) for v in row if v is not None]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _pptx_text(path: str) -> str:
    try:
        from pptx import Presentation
    except ImportError:
        return "(pptx extraction requires python-pptx: uv sync --extra llm)"
    try:
        prs = Presentation(path)
    except Exception:
        return "(deck unreadable)"
    lines: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        lines.append(f"--- Slide {i} ---")
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and text.strip():
                lines.append(text.strip())
    return "\n".join(lines)


def deliverable_text(path: str) -> str:
    """Text rendering of one deliverable file."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        text = docx_text(path) or "(document unreadable)"
    elif ext in (".xlsx", ".xlsm"):
        text = _xlsx_text(path)
    elif ext == ".pptx":
        text = _pptx_text(path)
    elif ext in (".md", ".txt", ".csv", ".json"):
        try:
            with open(path, errors="replace") as f:
                text = f.read()
        except Exception:
            text = "(unreadable)"
    else:
        text = f"(binary {ext} file; not rendered)"
    if len(text) > _MAX_CHARS_PER_FILE:
        text = text[:_MAX_CHARS_PER_FILE] + "\n(truncated)"
    return text


def submission_text(directory: str) -> str:
    """Text rendering of a whole submission directory (one player, one task).

    report.json is excluded: it is harness plumbing on the worker side and
    absent from gold, so showing it would mark which side is which.
    """
    if not os.path.isdir(directory):
        return "(no submission)"
    parts: list[str] = []
    for name in sorted(os.listdir(directory)):
        p = os.path.join(directory, name)
        if not os.path.isfile(p) or name.startswith("._") or name == ".DS_Store":
            continue
        if name == "report.json":
            continue
        parts.append(f"===== {name} =====\n{deliverable_text(p)}")
    return "\n\n".join(parts) if parts else "(empty submission)"
