"""Per-bundle configuration.

A new bundle needs a config entry, not code changes: anchors, expected counts,
where its ``category`` can be joined from, and how its task directories are
named. Config lives in ``configs/bundles.yaml`` keyed by the bundle directory
basename.

The acceptance workflow is *first run observes, second run asserts*: a bundle
with no ``expected`` block for a mode reports its counts so you can record
them; once recorded, every subsequent run is checked against them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml


@dataclass(frozen=True)
class AnchorSpec:
    """A hand-picked task with distinctive assertions, checked on every run."""

    task_id: str
    prompt_contains: list[str] = field(default_factory=list)
    prompt_excludes: list[str] = field(default_factory=list)
    deliverables: list[str] | None = None
    references_exclude: list[str] = field(default_factory=list)
    gold_nonempty: bool = True


@dataclass(frozen=True)
class ExpectedCounts:
    """Recorded accountable counts for one mode of one bundle."""

    tasks: int
    clean: int
    flagged: int


@dataclass(frozen=True)
class BundleConfig:
    name: str
    category_from: str | None = None
    occupation_map: str | None = None
    task_glob: str = "ME-*"
    expected: dict[str, ExpectedCounts] = field(default_factory=dict)
    anchors: list[AnchorSpec] = field(default_factory=list)


def _resolve_path(value: str | None, config_path: str) -> str | None:
    """Paths in the config resolve relative to the config file's directory."""
    if value is None:
        return None
    if os.path.isabs(value):
        return value
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(config_path)), value))


def load_bundle_config(config_path: str, bundle_dir: str) -> BundleConfig | None:
    """Load the config block for a bundle, keyed by its directory basename.

    Returns None when the file or the block does not exist — running an
    unconfigured bundle is allowed (that is the observing first run).
    """
    if not os.path.isfile(config_path):
        return None
    with open(config_path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    bundles: dict[str, Any] = data.get("bundles") or {}
    name = os.path.basename(os.path.abspath(bundle_dir))
    block = bundles.get(name)
    if block is None:
        return None
    expected = {
        mode: ExpectedCounts(**counts) for mode, counts in (block.get("expected") or {}).items()
    }
    anchors = [
        AnchorSpec(
            task_id=a["task_id"],
            prompt_contains=list(a.get("prompt_contains") or []),
            prompt_excludes=list(a.get("prompt_excludes") or []),
            deliverables=a.get("deliverables"),
            references_exclude=list(a.get("references_exclude") or []),
            gold_nonempty=bool(a.get("gold_nonempty", True)),
        )
        for a in (block.get("anchors") or [])
    ]
    return BundleConfig(
        name=name,
        category_from=_resolve_path(block.get("category_from"), config_path),
        occupation_map=_resolve_path(block.get("occupation_map"), config_path),
        task_glob=block.get("task_glob", "ME-*"),
        expected=expected,
        anchors=anchors,
    )
