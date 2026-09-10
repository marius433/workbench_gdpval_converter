"""Acceptance checks for an export — run after every conversion.

Structural checks always run (junk files, task dir completeness). Count
checks run only when the bundle config records ``expected`` counts for the
mode (*first run observes, second run asserts*). Anchor checks run when the
anchor task is present in the export.

Failures are printed and returned; flags inside the export are review queues,
not errors, and are never failures here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .config import AnchorSpec, BundleConfig


@dataclass
class AcceptanceReport:
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _task_dirs(export_dir: str) -> list[str]:
    return sorted(
        d
        for d in os.listdir(export_dir)
        if os.path.isdir(os.path.join(export_dir, d))
    )


def _check_structure(export_dir: str, report: AcceptanceReport) -> None:
    junk = [
        os.path.join(root, f)
        for root, _, files in os.walk(export_dir)
        for f in files
        if f == ".DS_Store" or f.startswith("._")
    ]
    if junk:
        report.failures.append(f"{len(junk)} macOS junk file(s) in export, e.g. {junk[0]}")
    for d in _task_dirs(export_dir):
        tdir = os.path.join(export_dir, d)
        for required in ("task.json", "references"):
            if not os.path.exists(os.path.join(tdir, required)):
                report.failures.append(f"{d}: missing {required}")
        if not os.path.isdir(os.path.join(tdir, "gold")):
            # gold/ is absent exactly when the source golden/ was missing,
            # which the conversion flags; surface it here too.
            report.failures.append(f"{d}: missing gold/")


def _check_counts(
    export_dir: str, cfg: BundleConfig, mode: str, report: AcceptanceReport
) -> None:
    with open(os.path.join(export_dir, "manifest.json")) as f:
        manifest = json.load(f)
    expected = cfg.expected.get(mode)
    observed = (manifest["tasks"], manifest["clean"], manifest["flagged"])
    if expected is None:
        report.notes.append(
            f"no expected counts recorded for mode '{mode}' — observed "
            f"tasks={observed[0]} clean={observed[1]} flagged={observed[2]}; "
            "record them in configs/bundles.yaml to make the next run accountable"
        )
        return
    want = (expected.tasks, expected.clean, expected.flagged)
    if observed != want:
        report.failures.append(
            f"counts mismatch for mode '{mode}': observed tasks/clean/flagged "
            f"{observed} != recorded {want}"
        )


def _check_anchor(export_dir: str, anchor: AnchorSpec, report: AcceptanceReport) -> None:
    tdir = os.path.join(export_dir, anchor.task_id)
    if not os.path.isdir(tdir):
        report.notes.append(
            f"anchor {anchor.task_id} not present in this export (mode split renames "
            "children); anchor checks skipped"
        )
        return
    with open(os.path.join(tdir, "task.json")) as f:
        task = json.load(f)
    prompt = task["prompt"]
    for needle in anchor.prompt_contains:
        if needle not in prompt:
            report.failures.append(f"anchor {anchor.task_id}: prompt lacks {needle!r}")
    for needle in anchor.prompt_excludes:
        if needle in prompt:
            report.failures.append(f"anchor {anchor.task_id}: prompt contains {needle!r}")
    if anchor.deliverables is not None and task["deliverables"] != anchor.deliverables:
        report.failures.append(
            f"anchor {anchor.task_id}: deliverables {task['deliverables']} != "
            f"{anchor.deliverables}"
        )
    refs = set(task["reference_files"])
    for name in anchor.references_exclude:
        if any(os.path.basename(r) == name for r in refs):
            report.failures.append(
                f"anchor {anchor.task_id}: references include excluded file {name}"
            )
    if anchor.gold_nonempty and not task["gold_files"]:
        report.failures.append(f"anchor {anchor.task_id}: gold is empty")


def check_export(
    export_dir: str, cfg: BundleConfig | None, mode: str
) -> AcceptanceReport:
    """Run all applicable acceptance checks on a written export."""
    report = AcceptanceReport()
    _check_structure(export_dir, report)
    if cfg is None:
        report.notes.append(
            "bundle has no config block — structural checks only; add a block to "
            "configs/bundles.yaml with expected counts and an anchor task"
        )
        return report
    _check_counts(export_dir, cfg, mode, report)
    for anchor in cfg.anchors:
        _check_anchor(export_dir, anchor, report)
    return report
