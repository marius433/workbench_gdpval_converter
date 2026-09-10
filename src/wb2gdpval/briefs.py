"""Brief detection: files whose text belongs in the prompt.

WorkBench hides the brief in the environment; the conversion *relocates* the
brief text into the prompt (concatenate and clean only — never rewrite).
Detection order, authoritative:

1. Files named in the description (``reference/...`` pointers).
2. Contents of a ``01_brief/`` folder.
3. Name-pattern match (email / request / brief / kick / note_from /
   memo_from), punctuation-insensitive.

No brief found is flagged, not fatal — some tasks carry the brief verbatim in
the description.
"""

from __future__ import annotations

import os
import re

from .textio import norm_name

BRIEF_PATTERNS = ("email", "request", "brief", "kick", "note_from", "memo_from")
_BRIEF_PATTERNS_NORM = tuple(re.sub(r"[^a-z0-9]", "", k) for k in BRIEF_PATTERNS)
BRIEF_EXTENSIONS = (".docx", ".txt", ".md", ".msg")

_REF_POINTER = re.compile(r"`?reference/([A-Za-z0-9_\-\. /]+?\.(?:docx|txt|md|msg))`?")


def find_briefs(refdir: str, description: str) -> list[str]:
    """Return brief file paths, de-duplicated, in detection order."""
    briefs: list[str] = []
    for m in _REF_POINTER.finditer(description):
        p = os.path.join(refdir, m.group(1))
        if os.path.exists(p):
            briefs.append(p)
    bdir = os.path.join(refdir, "01_brief")
    if os.path.isdir(bdir):
        for f in sorted(os.listdir(bdir)):
            if f.lower().endswith(BRIEF_EXTENSIONS):
                briefs.append(os.path.join(bdir, f))
    if not briefs:
        for f in sorted(os.listdir(refdir)):
            p = os.path.join(refdir, f)
            if (
                os.path.isfile(p)
                and f.lower().endswith(BRIEF_EXTENSIONS)
                and any(k in norm_name(f) for k in _BRIEF_PATTERNS_NORM)
            ):
                briefs.append(p)
    seen: set[str] = set()
    out: list[str] = []
    for p in briefs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
