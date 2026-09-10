"""Prompt assembly and deliverable parsing."""

from __future__ import annotations

import os
import re

from .textio import clean_email, docx_text, msg_text

# Deliverable filename shape recognised in prompts.
FNAME = r"[A-Za-z0-9_\-]+\.(?:docx|xlsx|pptx|pdf|md|csv)"

_SAVE_AS = re.compile(
    r"[Ss]ave your deliverable\(?s?\)? as (.+?)(?:\bin your output|$)", re.S
)
_DELIVERABLES_HEADING = re.compile(r"Deliverables?\s*:(.+?)(?:\n\n|$)", re.S)


def parse_deliverables(description: str) -> list[str]:
    """Deliverable filenames from the task description.

    Priority: the backticked list in the "Save your deliverable(s) as ..."
    sentence; then a "Deliverables:" block; then any filename-shaped token.
    ``report.json`` is harness plumbing and never a deliverable.
    """
    m = _SAVE_AS.search(description)
    if m:
        got = re.findall(r"`([^`]+)`", m.group(1))
        if got:
            return got
    m = _DELIVERABLES_HEADING.search(description)
    scope = m.group(1) if m else description
    got = [f for f in re.findall(FNAME, scope) if f != "report.json"]
    return list(dict.fromkeys(got))


def brief_text(path: str) -> str | None:
    """Extract a brief file's text by extension (.docx / .msg / plain)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return docx_text(path)
    if ext == ".msg":
        return msg_text(path)
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return None


def build_prompt(
    description: str, briefs: list[str], refdir: str
) -> tuple[str, set[str], list[str]]:
    """Merge the description with the cleaned text of each brief.

    Returns (prompt, consumed_abs_paths, flags). Pointers at consumed brief
    files are rewritten to "the brief below"; nothing else in the prose is
    ever edited.
    """
    parts = [description]
    consumed: set[str] = set()
    flags: list[str] = []
    for b in briefs:
        t = brief_text(b)
        if t is None:
            flags.append(f"brief unreadable: {os.path.relpath(b, refdir)}")
            continue
        parts.append("--- Brief: " + os.path.basename(b) + " ---\n" + clean_email(t))
        consumed.add(os.path.abspath(b))
    if not briefs:
        flags.append("no brief file found; prompt = description only")
    prompt = "\n\n".join(parts)
    # brief files are inlined above, so rewrite pointers at them
    for b in briefs:
        rel = os.path.relpath(b, refdir)
        prompt = prompt.replace("`reference/" + rel + "`", "the brief below")
        prompt = prompt.replace("reference/" + rel, "the brief below")
    return prompt, consumed, flags
