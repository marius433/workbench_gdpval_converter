"""Mode B: split a converted task by deliverable.

A task whose prompt asks for more than one deliverable becomes one child task
per deliverable — the goldens already exist, so each child is sliced from the
parent deterministically (no model): same references, the gold file for that
deliverable only, the full prompt plus a clearly-marked mechanical scope note.

Expert prose is never rewritten. The scope note is conversion machinery of the
same kind as the GDPval grading preamble: appended, labelled, and disclosed in
task.json. Interdependence between deliverables (a cover note that explains a
deck, a rubric criterion spanning both files) cannot be sliced mechanically —
it is detected by filename/stem mention and flagged for the human review
queue, never edited away.
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import replace

from .models import TaskConversion

SCOPE_NOTE_HEADER = "--- Conversion scope note ---"

_SCOPE_NOTE = (
    "{header}\n"
    "This task covers exactly one deliverable of the original assignment: produce only "
    "`{target}`. Any other deliverable filenames mentioned above are out of scope here "
    "and will not be graded."
)

_RUBRIC_SCOPE_NOTE = (
    "Split-scope note: only `{target}` is graded in this task. Criteria that address "
    "other deliverables of the original assignment do not apply; criteria that span "
    "deliverables apply only insofar as they can be judged from `{target}` alone.\n\n"
)


def _stem(name: str) -> str:
    return os.path.splitext(name)[0]


def _mentions(text: str, filename: str) -> bool:
    """True if text mentions the filename or its stem as a whole token."""
    return any(re.search(re.escape(needle), text) for needle in (filename, _stem(filename)))


def _child_id(parent_id: str, index: int) -> str:
    return f"{parent_id}-D{index:02d}"


def split_task(parent: TaskConversion) -> list[TaskConversion]:
    """Split one conversion into per-deliverable children.

    A task with zero or one deliverable passes through unchanged (same id).
    Children carry ``parent_task_id`` and ``sibling_deliverables`` provenance.
    """
    # report.json is harness plumbing, never a GDPval deliverable — a prompt
    # that lists it must not produce a child graded on it. (Mode A keeps the
    # parsed list untouched; only the split acts on the cleaned view.)
    real = [d for d in parent.deliverables if d.lower() != "report.json"]
    if len(real) <= 1:
        return [parent]

    children: list[TaskConversion] = []
    for i, target in enumerate(real, start=1):
        siblings = [d for d in real if d != target]
        flags = list(parent.flags)
        flags.append(f"split from {parent.task_id} ({len(real)} deliverables)")
        if len(real) != len(parent.deliverables):
            flags.append("report.json listed as a deliverable in prompt; excluded from split")

        # Gold slice: exact name match first, then stem match (renderings).
        gold = [g for g in parent.gold if g.name == target]
        if not gold:
            gold = [g for g in parent.gold if _stem(g.name) == _stem(target)]
        gold_alt = [g for g in parent.gold_alt if _stem(g.name) == _stem(target)]
        if not gold:
            flags.append(f"gold missing for split deliverable {target}")

        prompt = (
            parent.prompt + "\n\n" + _SCOPE_NOTE.format(header=SCOPE_NOTE_HEADER, target=target)
        )

        # Rubric travels whole with a scope preamble; slicing prose per
        # deliverable would rewrite the expert's words.
        rubric = copy.deepcopy(parent.rubric)
        if rubric.pretty:
            rubric.pretty = _RUBRIC_SCOPE_NOTE.format(target=target) + rubric.pretty

        # Interdependence detection: sibling deliverables named in the rubric
        # (or its JSON rendering) mean the criteria likely span deliverables.
        mentioned = sorted(
            {
                s
                for s in siblings
                if _mentions(rubric.pretty_source, s) or _mentions(rubric.json_text, s)
            }
        )
        if mentioned:
            flags.append(
                "deliverables may be interdependent; rubric mentions sibling(s): "
                + ", ".join(mentioned)
                + "; needs ME review"
            )

        children.append(
            replace(
                parent,
                task_id=_child_id(parent.task_id, i),
                prompt=prompt,
                deliverables=[target],
                gold=gold,
                gold_alt=gold_alt,
                rubric=rubric,
                flags=flags,
                parent_task_id=parent.task_id,
                sibling_deliverables=siblings,
            )
        )
    return children
