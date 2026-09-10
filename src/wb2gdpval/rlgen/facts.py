"""Fact resolution against the environment's actual files.

The validator never trusts an asserted fact: a ``FactRef`` either resolves
against the real reference files or the check fails. Spreadsheet refs read a
cell with openpyxl (cached values, since openpyxl never computes formulas);
quote refs require the exact substring in the file's extracted text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

import openpyxl

from ..textio import docx_text, msg_text
from .schema import FactRef


@dataclass(frozen=True)
class ResolvedFact:
    ref: FactRef
    value: float | str | bool | None  # cell refs only; quotes resolve to None
    ok: bool
    detail: str


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=256)
def _file_text(path: str) -> str | None:
    """Extracted searchable text of a reference file, best effort."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return docx_text(path)
    if ext == ".msg":
        return msg_text(path)
    if ext in (".xlsx", ".xlsm"):
        try:
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            chunks: list[str] = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    chunks.extend(str(v) for v in row if v is not None)
            return "\n".join(chunks)
        except Exception:
            return None
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except Exception:
        return None


def _read_cell(path: str, sheet: str, cell: str) -> tuple[object, str | None]:
    """Returns (value, error). data_only=True reads cached formula values."""
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        return None, f"workbook unreadable: {type(e).__name__}"
    if sheet not in wb.sheetnames:
        return None, f"sheet {sheet!r} not in workbook (has {wb.sheetnames})"
    try:
        value = wb[sheet][cell].value
    except Exception as e:
        return None, f"cell {cell!r} unreadable: {type(e).__name__}"
    if value is None:
        return None, f"cell {sheet}!{cell} is empty (or formula value not cached)"
    return value, None


def _normalise_ref_path(file: str) -> str:
    """Model-written paths may carry the exported ``references/`` (or sandbox
    ``reference/``) prefix; the canonical key is the path relative to the
    reference dir. Also refuses path traversal outside it."""
    rel = file.replace("\\", "/").lstrip("/")
    first, _, rest = rel.partition("/")
    if first in ("references", "reference") and rest:
        rel = rest
    norm = os.path.normpath(rel)
    if norm.startswith(".."):
        raise ValueError(f"ref path escapes the reference dir: {file!r}")
    return norm


def resolve_fact(ref: FactRef, refdir: str) -> ResolvedFact:
    """Resolve one FactRef against the reference directory."""
    try:
        rel = _normalise_ref_path(ref.file)
    except ValueError as e:
        return ResolvedFact(ref, None, False, str(e))
    path = os.path.join(refdir, rel)
    if not os.path.isfile(path):
        return ResolvedFact(ref, None, False, f"file not found: {ref.file}")
    if ref.cell:
        if not ref.sheet:
            return ResolvedFact(ref, None, False, "cell ref without sheet name")
        value, err = _read_cell(path, ref.sheet, ref.cell)
        if err:
            return ResolvedFact(ref, None, False, err)
        if isinstance(value, bool | int | float | str):
            return ResolvedFact(ref, value, True, f"{ref.sheet}!{ref.cell} = {value!r}")
        return ResolvedFact(ref, None, False, f"unsupported cell type {type(value).__name__}")
    if ref.quote:
        text = _file_text(path)
        if text is None:
            return ResolvedFact(ref, None, False, f"file text unextractable: {ref.file}")
        if _norm_ws(ref.quote) in _norm_ws(text):
            return ResolvedFact(ref, None, True, "quote found")
        return ResolvedFact(ref, None, False, f"quote not found in {ref.file}")
    return ResolvedFact(ref, None, False, "ref has neither cell nor quote")
