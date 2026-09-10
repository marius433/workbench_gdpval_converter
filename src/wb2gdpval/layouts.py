"""Task-directory layout detection.

Two layouts ship in the wild:

- **pack** — the WorkBench pack layout: ``harness/pack/task.yaml`` +
  ``harness/pack/grading.yaml`` + ``harness/reference/`` + ``golden/``.
- **review** — the flat review-format layout: ``user_prompt/user_prompt.md`` +
  ``grader/grading.yaml`` + ``reference/`` + ``golden/``. This layout drops
  ``task.yaml`` (and with it ``category``), which can be joined back from a
  pack-layout bundle of the same tasks via ``--category-from``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

# Review-format packaging suffixes on task dir names: ME-A-01-P20_vF -> ME-A-01-P20
_SUFFIX = re.compile(r"_(?:vF|evals)(?:_\d+)?$")


def normalise_tid(name: str) -> str:
    """Strip review-format packaging suffixes from a task dir name."""
    return _SUFFIX.sub("", name)


@dataclass(frozen=True)
class TaskLayout:
    """Locations of one task's pieces, independent of layout kind."""

    kind: str  # 'pack' | 'review'
    grading: str  # grading.yaml path
    refdir: str  # reference files directory
    gold: str  # golden/ directory
    task_yaml: str | None = None  # pack only
    user_prompt: str | None = None  # review only


def detect_layout(task_dir: str) -> TaskLayout | None:
    """Locate a task's pieces, or None if the layout is unrecognised."""
    pack = os.path.join(task_dir, "harness", "pack")
    if os.path.isfile(os.path.join(pack, "task.yaml")):
        return TaskLayout(
            kind="pack",
            task_yaml=os.path.join(pack, "task.yaml"),
            grading=os.path.join(pack, "grading.yaml"),
            refdir=os.path.join(task_dir, "harness", "reference"),
            gold=os.path.join(task_dir, "golden"),
        )
    up = os.path.join(task_dir, "user_prompt", "user_prompt.md")
    if os.path.isfile(up):
        return TaskLayout(
            kind="review",
            user_prompt=up,
            grading=os.path.join(task_dir, "grader", "grading.yaml"),
            refdir=os.path.join(task_dir, "reference"),
            gold=os.path.join(task_dir, "golden"),
        )
    return None


def md_description(path: str) -> str:
    """The '## Description' section of a review-format user_prompt.md only.

    Everything from the next '## ' heading on is the system addendum
    (report.json format plumbing) and is excluded from the prompt, exactly as
    task.yaml's ``system_addendum`` is in the pack layout.
    """
    with open(path) as f:
        txt = f.read()
    m = re.search(r"^##[ \t]+Description[ \t]*$(.*?)(?=^##[ \t]+|\Z)", txt, re.M | re.S)
    return (m.group(1) if m else txt).strip()


def joined_category(task_id: str, category_from: str | None) -> str | None:
    """Read ``category`` back from a pack-layout bundle of the same tasks.

    The review format drops task.yaml; this is a lookup of recorded fact,
    never a guess.
    """
    if not category_from:
        return None
    y = os.path.join(category_from, task_id, "harness", "pack", "task.yaml")
    if not os.path.isfile(y):
        return None
    with open(y) as f:
        data = yaml.safe_load(f) or {}
    category = data.get("category")
    return str(category) if category is not None else None
