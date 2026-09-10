"""Data model for a converted task.

``TaskConversion`` is the in-memory result of converting one WorkBench task:
everything needed to write a GDPval task directory, a ``gdpval_tasks.jsonl``
row, and a conversion-report row. Mode B (split-by-deliverable) derives child
conversions from a parent conversion without touching the source again.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .rubric import RubricBundle

# GDPval distribution bands (openai/gdpval, 220 rows). Positions are recorded
# per task as disclosure, never edited.
PROMPT_CHARS_MIN = 617
PROMPT_CHARS_MAX = 6620
REFERENCE_FILES_MAX = 17


@dataclass(frozen=True)
class RefFile:
    """One reference file scheduled for copy into references/.

    ``msg_txt`` carries the extracted text when a source .msg is re-emitted
    as .txt (``rel`` then already carries the .txt name).
    """

    src: str
    rel: str
    msg_txt: str | None = None


@dataclass(frozen=True)
class GoldFile:
    """One golden file scheduled for copy into gold/ or gold_alt/."""

    src: str
    name: str


@dataclass
class TaskConversion:
    """Complete conversion result for one task (or one split child)."""

    task_id: str
    source_dir: str
    layout: str
    occupation: str
    sector: str
    occupation_source: str
    category: str
    category_source: str
    prompt: str
    deliverables: list[str]
    refs: list[RefFile]
    gold: list[GoldFile]
    gold_alt: list[GoldFile]
    briefs_merged: list[str]
    rubric: RubricBundle
    flags: list[str]
    # Whether the source golden/ directory existed (gold/ is materialised in
    # the export exactly when it did, matching the 1.4.0 behaviour).
    golden_present: bool = True
    # Split (Mode B) provenance — None/empty for Mode A tasks.
    parent_task_id: str | None = None
    sibling_deliverables: list[str] = field(default_factory=list)

    @property
    def reference_rels(self) -> list[str]:
        return sorted(r.rel for r in self.refs)

    @property
    def converted_rels(self) -> list[str]:
        return sorted(r.rel for r in self.refs if r.msg_txt is not None)

    @property
    def gold_names(self) -> list[str]:
        return sorted(g.name for g in self.gold)

    @property
    def gold_alt_names(self) -> list[str]:
        return sorted(g.name for g in self.gold_alt)

    def to_task_json(self, source_bundle_name: str) -> dict[str, object]:
        """The per-task ``task.json`` sidecar: conversion provenance that never
        enters the dataset row."""
        d: dict[str, object] = dict(
            task_id=self.task_id,
            source=f"WorkBench {source_bundle_name}",
            source_dir=self.source_dir,
            layout=self.layout,
            occupation=self.occupation,
            sector=self.sector,
            occupation_source=self.occupation_source,
            category=self.category,
            category_source=self.category_source,
            prompt=self.prompt,
            deliverables=self.deliverables,
            reference_files=self.reference_rels,
            n_reference_files=len(self.refs),
            gold_files=self.gold_names,
            gold_alt=self.gold_alt_names,
            briefs_merged=self.briefs_merged,
            converted_files=self.converted_rels,
            prompt_chars=len(self.prompt),
            prompt_outside_gdpval_band=not (
                PROMPT_CHARS_MIN <= len(self.prompt) <= PROMPT_CHARS_MAX
            ),
            refs_outside_gdpval_band=len(self.refs) > REFERENCE_FILES_MAX,
            rubric_decoupled=True,
            rubric_residual_coupling=self.rubric.residual[:12],
            n_rubric_residual=len(self.rubric.residual),
            review_flags=self.flags,
        )
        if self.parent_task_id is not None:
            d["parent_task_id"] = self.parent_task_id
            d["sibling_deliverables"] = self.sibling_deliverables
        return d

    def to_gdpval_row(self) -> dict[str, object]:
        """One ``gdpval_tasks.jsonl`` row. Field names follow openai/gdpval;
        ``input_dir``/``task_dir`` are ours (the runner must upload
        references/ as a DIRECTORY, not a file list)."""
        return dict(
            task_id=self.task_id,
            sector=self.sector,
            occupation=self.occupation,
            prompt=self.prompt,
            reference_files=[os.path.join("references", r) for r in self.reference_rels],
            deliverable_files=[os.path.join("gold", f) for f in self.gold_names],
            rubric_pretty=self.rubric.pretty,
            rubric_json=self.rubric.json_text,
            input_dir="references",
            task_dir=self.task_id,
        )
