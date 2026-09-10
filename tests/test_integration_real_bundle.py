"""Integration check against the real ME-A-01 bundle (local machines only).

Skipped when the bundle is not present. Asserts the recorded accountable
counts (process manual: final run 2026-09-02, converter 1.4.0, reproduced
byte-identically by 2.0.0) and the ME-A-01-P20 anchor.
"""

from __future__ import annotations

import json
import os

import pytest
from conftest import REAL_BUNDLE, REAL_CATEGORY_FROM

from wb2gdpval.export import run_export

pytestmark = pytest.mark.skipif(
    not (os.path.isdir(REAL_BUNDLE) and os.path.isdir(REAL_CATEGORY_FROM)),
    reason="real ME-A-01 bundles not present on this machine",
)


@pytest.fixture(scope="module")
def real_export(tmp_path_factory: pytest.TempPathFactory):
    return run_export(
        bundle_dir=REAL_BUNDLE,
        exports_root=str(tmp_path_factory.mktemp("real_exports")),
        category_from=REAL_CATEGORY_FROM,
    )


def test_recorded_accountable_counts(real_export) -> None:
    m = real_export.manifest
    assert (m["tasks"], m["clean"], m["flagged"]) == (50, 11, 39)
    assert m["layouts"] == {"review": 50}
    assert m["unmapped_occupations"] == []
    assert m["rubrics_present"] == 50
    assert m["flag_summary"]["no brief file found"] == 0
    assert m["flag_summary"]["deliverables taken from grading.yaml"] == 9
    assert m["flag_summary"]["golden missing"] == 0


def test_anchor_task_p20(real_export) -> None:
    with open(os.path.join(real_export.out_dir, "ME-A-01-P20", "task.json")) as f:
        t = json.load(f)
    assert "Marcus wants the Phase 1 site visit plan" in t["prompt"]
    assert t["deliverables"] == [
        "PetroNusa_Phase1_CoveringNote.docx",
        "PetroNusa_Phase1_SiteVisitPlan.xlsx",
    ]
    assert not any(
        os.path.basename(r) == "SiteVisitPlan_Request.docx" for r in t["reference_files"]
    )
    assert t["gold_files"]


def test_split_mode_recorded_counts(tmp_path) -> None:
    result = run_export(
        bundle_dir=REAL_BUNDLE,
        exports_root=str(tmp_path / "exports"),
        mode="split",
        category_from=REAL_CATEGORY_FROM,
    )
    m = result.manifest
    assert (m["tasks"], m["clean"], m["flagged"]) == (85, 8, 77)
    assert m["split"]["children"] == 69
    assert m["flag_summary"]["gold missing for split deliverable"] == 0
