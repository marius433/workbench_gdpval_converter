"""Mode A core: convert one WorkBench task directory into a TaskConversion.

Deterministic: no LLM calls. Ambiguities are flagged, not guessed. Flag order
is stable and matches the 1.4.0 script so recorded per-bundle runs reproduce.
"""

from __future__ import annotations

import os
import re

import yaml

from .briefs import find_briefs
from .gold import select_gold
from .layouts import TaskLayout, detect_layout, joined_category, md_description, normalise_tid
from .models import GoldFile, RefFile, TaskConversion
from .occupations import OccupationResolver
from .prompts import build_prompt, parse_deliverables
from .rubric import load_rubrics
from .textio import msg_text

_JUNK_PREFIX = "._"
_FOLDER_POINTER = re.compile(r"\b\d\d_[A-Za-z]+\b")


def _read_description_and_category(
    layout: TaskLayout, task_id: str, category_from: str | None, flags: list[str]
) -> tuple[str, str, str]:
    """Returns (description, category, category_source)."""
    if layout.kind == "pack":
        assert layout.task_yaml is not None
        with open(layout.task_yaml) as f:
            y = yaml.safe_load(f) or {}
        return (y.get("description") or "").strip(), y.get("category", ""), "pack"
    assert layout.user_prompt is not None
    desc = md_description(layout.user_prompt)
    cat = joined_category(task_id, category_from) or ""
    if cat:
        assert category_from is not None
        source = f"joined:{os.path.basename(os.path.abspath(category_from))}"
    else:
        source = "unavailable"
        flags.append("category unavailable in this layout; occupation unmapped")
    return desc, cat, source


def _plan_references(refdir: str, consumed: set[str], flags: list[str]) -> list[RefFile]:
    """Plan the references/ copy: everything minus consumed briefs and macOS
    junk. Outlook .msg files are re-emitted as .txt (a .msg is not openable
    by sandbox tooling); unreadable ones ship as-is, flagged."""
    refs: list[RefFile] = []
    for root, _, files in os.walk(refdir):
        for f in files:
            p = os.path.abspath(os.path.join(root, f))
            if p in consumed or f.startswith(_JUNK_PREFIX) or f == ".DS_Store":
                continue
            rel = os.path.relpath(p, refdir)
            if f.lower().endswith(".msg"):
                t = msg_text(p)
                if t:
                    refs.append(RefFile(src=p, rel=os.path.splitext(rel)[0] + ".txt", msg_txt=t))
                    continue
                flags.append(f".msg unreadable, shipped as-is: {rel}")
            refs.append(RefFile(src=p, rel=rel))
    return refs


def _flag_dangling_folders(
    prompt: str, refs: list[RefFile], briefs: list[str], refdir: str, flags: list[str]
) -> None:
    """A prompt may cite a folder that conversion emptied (its brief was
    inlined) or one the source renamed without updating the text. Flag either
    — never rewrite: editing prose would mangle the expert's wording."""
    consumed_folders = {os.path.dirname(os.path.relpath(b, refdir)) for b in briefs}
    present = {r.rel.split(os.sep)[0] for r in refs if os.sep in r.rel}
    for folder in sorted(set(_FOLDER_POINTER.findall(prompt))):
        if folder in present:
            continue
        why = (
            "consumed (brief inlined)"
            if folder in consumed_folders
            else "not present in references"
        )
        flags.append(f"prompt cites folder {folder}/ - {why}; pointer dangling")


def _resolve_deliverables(description: str, grading_path: str, flags: list[str]) -> list[str]:
    """Deliverables from the prompt; fallback to grading.yaml ``file_exists``
    targets (flagged). report.json is never a deliverable."""
    dl = parse_deliverables(description)
    if dl:
        return dl
    with open(grading_path) as f:
        grading = yaml.safe_load(f)
    structural = grading.get("structural") or []
    if isinstance(structural, list):
        dl = [
            c["target"]
            for c in structural
            if c.get("check_type") == "file_exists"
            and str(c.get("target", "")).lower() != "report.json"
        ]
    if dl:
        flags.append("deliverables taken from grading.yaml, not prompt")
    else:
        flags.append("deliverable filenames not parsed")
    return dl


def _plan_gold(
    gold_dir: str, deliverable_names: set[str], flags: list[str]
) -> tuple[list[GoldFile], list[GoldFile], bool]:
    if not os.path.isdir(gold_dir):
        flags.append("golden missing")
        return [], [], False
    kept, quarantined = select_gold(gold_dir, deliverable_names)
    if not kept:
        flags.append("no gradeable gold artifact after hygiene")
    return (
        [GoldFile(src=os.path.join(gold_dir, f), name=f) for f in kept],
        [GoldFile(src=os.path.join(gold_dir, f), name=f) for f in quarantined],
        True,
    )


def convert_task(
    task_dir: str,
    resolver: OccupationResolver,
    category_from: str | None = None,
) -> TaskConversion | None:
    """Convert one task directory. Returns None for an unrecognised layout."""
    source_dir = os.path.basename(os.path.normpath(task_dir))
    task_id = normalise_tid(source_dir)
    flags: list[str] = []
    layout = detect_layout(task_dir)
    if layout is None:
        return None

    description, category, category_source = _read_description_and_category(
        layout, task_id, category_from, flags
    )
    occupation, sector, occupation_source = resolver.resolve(task_id, category)

    briefs = find_briefs(layout.refdir, description)
    prompt, consumed, brief_flags = build_prompt(description, briefs, layout.refdir)
    flags.extend(brief_flags)

    refs = _plan_references(layout.refdir, consumed, flags)
    _flag_dangling_folders(prompt, refs, briefs, layout.refdir, flags)

    deliverables = _resolve_deliverables(description, layout.grading, flags)
    gold, gold_alt, golden_present = _plan_gold(layout.gold, set(deliverables), flags)

    rubric = load_rubrics(layout.grading)
    flags.extend(rubric.flags)

    return TaskConversion(
        task_id=task_id,
        source_dir=source_dir,
        layout=layout.kind,
        occupation=occupation,
        sector=sector,
        occupation_source=occupation_source,
        category=category,
        category_source=category_source,
        prompt=prompt,
        deliverables=deliverables,
        refs=refs,
        gold=gold,
        gold_alt=gold_alt,
        briefs_merged=[os.path.relpath(b, layout.refdir) for b in briefs],
        rubric=rubric,
        flags=flags,
        golden_present=golden_present,
    )
