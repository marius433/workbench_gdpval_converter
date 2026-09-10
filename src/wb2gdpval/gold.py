"""Gold hygiene.

GDPval's Elo scale is anchored to human expert deliverables entering the
pairwise comparison as submissions, so gold must look like a model
submission: no report.json, exactly one rendering per artifact. Anything
dropped is quarantined in ``gold_alt/``, never deleted.
"""

from __future__ import annotations

import os

# Preference order when one artifact ships in several renderings.
_RENDERING_PREFERENCE = (".docx", ".xlsx", ".pptx", ".md", ".csv", ".pdf")


def select_gold(gold_dir: str, deliverable_names: set[str]) -> tuple[list[str], list[str]]:
    """Partition golden/ files into (kept, quarantined) basenames.

    report.json is WorkBench harness plumbing that indexes the gold answers —
    it is not an expert deliverable and must not enter a pairwise comparison.
    Where one artifact ships in several renderings exactly one is kept: a
    filename named as a deliverable wins, then the rendering preference order.
    """
    candidates = [
        f
        for f in sorted(os.listdir(gold_dir))
        if os.path.isfile(os.path.join(gold_dir, f)) and not f.startswith("._") and f != ".DS_Store"
    ]
    kept: list[str] = []
    quarantined: list[str] = []
    by_stem: dict[str, list[str]] = {}
    for f in candidates:
        if f.lower() == "report.json":
            quarantined.append(f)
            continue
        by_stem.setdefault(os.path.splitext(f)[0], []).append(f)
    for _stem, fs in by_stem.items():
        if len(fs) == 1:
            keep = fs[0]
        else:
            named = [f for f in fs if f in deliverable_names]
            keep = (
                named
                or sorted(
                    fs,
                    key=lambda f: (
                        _RENDERING_PREFERENCE.index(os.path.splitext(f)[1].lower())
                        if os.path.splitext(f)[1].lower() in _RENDERING_PREFERENCE
                        else len(_RENDERING_PREFERENCE)
                    ),
                )
            )[0]
        kept.append(keep)
        quarantined.extend(f for f in fs if f != keep)
    return sorted(kept), sorted(quarantined)
