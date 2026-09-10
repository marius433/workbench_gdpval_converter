"""End-to-end conversion tests on the synthetic bundle (both modes + CLI)."""

from __future__ import annotations

import json
import os

import pytest

from wb2gdpval.cli import main
from wb2gdpval.convert import convert_task
from wb2gdpval.export import run_export
from wb2gdpval.occupations import OccupationResolver
from wb2gdpval.split import split_task


def _resolver(bundle: str) -> OccupationResolver:
    r = OccupationResolver()
    r.load_maps_near(bundle)
    return r


class TestConvertTask:
    def test_review_layout_with_docx_brief(self, review_bundle: str, pack_bundle: str) -> None:
        conv = convert_task(
            os.path.join(review_bundle, "ME-Z-99-P01_vF"),
            _resolver(review_bundle),
            category_from=pack_bundle,
        )
        assert conv is not None
        assert conv.task_id == "ME-Z-99-P01"
        assert conv.layout == "review"
        # category joined back from the pack twin
        assert conv.category == "strategy"
        assert conv.category_source.startswith("joined:")
        assert conv.occupation == "Project Management Specialists"
        # brief inlined, header cleaned, pointer rewritten
        assert "--- Brief: Client_Email.docx ---" in conv.prompt
        assert "flag the anomaly" in conv.prompt
        assert "reference/01_brief/Client_Email.docx" not in conv.prompt
        assert "the brief below" in conv.prompt
        # consumed brief and junk excluded from references
        rels = conv.reference_rels
        assert rels == [os.path.join("02_data", "sales.xlsx")]
        assert conv.deliverables == ["memo.docx", "model.xlsx"]
        # gold hygiene: report.json + duplicate rendering quarantined
        assert conv.gold_names == ["memo.docx", "model.xlsx"]
        assert conv.gold_alt_names == ["memo.pdf", "report.json"]
        assert not any("no brief" in f for f in conv.flags)

    def test_review_layout_msg_brief_and_fallbacks(
        self, review_bundle: str, pack_bundle: str
    ) -> None:
        conv = convert_task(
            os.path.join(review_bundle, "ME-Z-99-P02_vF"),
            _resolver(review_bundle),
            category_from=pack_bundle,
        )
        assert conv is not None
        # per-task occupation override beats the category table
        assert conv.occupation == "Financial and Investment Analysts"
        assert conv.occupation_source == "task-override"
        assert conv.sector == "Finance and Insurance"
        # .msg brief inlined
        assert "--- Brief: Manager_Request.msg ---" in conv.prompt
        assert "flag the anomaly" in conv.prompt
        # deliverables fell back to grading.yaml, excluding report.json
        assert conv.deliverables == ["analysis.docx"]
        assert any("grading.yaml" in f for f in conv.flags)
        # dangling folder pointer flagged, never rewritten
        assert any("02_data/ - not present" in f for f in conv.flags)
        assert "02_data/" in conv.prompt
        # non-brief .msg re-emitted as .txt
        assert os.path.join("03_data", "note_archive.txt") in conv.converted_rels

    def test_pack_layout(self, pack_bundle: str) -> None:
        conv = convert_task(os.path.join(pack_bundle, "ME-Z-99-P01"), _resolver(pack_bundle))
        assert conv is not None
        assert conv.layout == "pack"
        assert conv.category == "strategy"
        assert conv.category_source == "pack"
        assert conv.deliverables == ["memo.docx", "model.xlsx"]

    def test_unrecognised_layout_returns_none(self, tmp_path) -> None:
        (tmp_path / "ME-Z-99-P03").mkdir()
        assert convert_task(str(tmp_path / "ME-Z-99-P03"), OccupationResolver()) is None


class TestSplit:
    def test_multi_deliverable_splits_with_gold_slice_and_flags(
        self, review_bundle: str, pack_bundle: str
    ) -> None:
        parent = convert_task(
            os.path.join(review_bundle, "ME-Z-99-P01_vF"),
            _resolver(review_bundle),
            category_from=pack_bundle,
        )
        assert parent is not None
        children = split_task(parent)
        assert [c.task_id for c in children] == ["ME-Z-99-P01-D01", "ME-Z-99-P01-D02"]
        d1, d2 = children
        assert d1.deliverables == ["memo.docx"]
        assert d1.gold_names == ["memo.docx"]
        assert d1.gold_alt_names == ["memo.pdf"]  # same-stem rendering travels
        assert d2.gold_names == ["model.xlsx"]
        assert d1.parent_task_id == "ME-Z-99-P01"
        assert d1.sibling_deliverables == ["model.xlsx"]
        # scope notes present, parent prompt intact underneath
        assert parent.prompt in d1.prompt
        assert "produce only `memo.docx`" in d1.prompt
        assert d1.rubric.pretty.startswith("Split-scope note:")
        # rubric names both files -> interdependence flagged
        assert any("may be interdependent" in f for f in d1.flags)
        # references identical to the parent's
        assert d1.reference_rels == parent.reference_rels

    def test_single_deliverable_passes_through(self, review_bundle: str, pack_bundle: str) -> None:
        parent = convert_task(
            os.path.join(review_bundle, "ME-Z-99-P02_vF"),
            _resolver(review_bundle),
            category_from=pack_bundle,
        )
        assert parent is not None
        assert split_task(parent) == [parent]

    def test_report_json_never_becomes_a_child(self, review_bundle: str, pack_bundle: str) -> None:
        parent = convert_task(
            os.path.join(review_bundle, "ME-Z-99-P02_vF"),
            _resolver(review_bundle),
            category_from=pack_bundle,
        )
        assert parent is not None
        parent.deliverables = ["analysis.docx", "report.json"]
        assert split_task(parent) == [parent]  # one real deliverable -> pass through


class TestRunExport:
    @pytest.fixture()
    def export(self, review_bundle: str, pack_bundle: str, tmp_path):
        return run_export(
            bundle_dir=review_bundle,
            exports_root=str(tmp_path / "exports"),
            category_from=pack_bundle,
        )

    def test_export_layout_and_manifest(self, export) -> None:
        out = export.out_dir
        for name in (
            "manifest.json",
            "gdpval_tasks.jsonl",
            "conversion_report.csv",
            "SHIP_NOTES.md",
            "stirrup_runner.py",
        ):
            assert os.path.isfile(os.path.join(out, name))
        m = export.manifest
        assert (m["tasks"], m["clean"]) == (2, 0)
        assert m["layouts"] == {"review": 2}
        assert m["msg_converted_to_txt"] == 1
        assert m["gold_alt_quarantined"] == 2
        assert m["unmapped_occupations"] == []

    def test_task_dirs_and_jsonl_schema(self, export) -> None:
        out = export.out_dir
        tdir = os.path.join(out, "ME-Z-99-P01")
        assert os.path.isfile(os.path.join(tdir, "task.json"))
        assert os.path.isfile(os.path.join(tdir, "rubric_source.md"))
        assert os.path.isfile(os.path.join(tdir, "references", "02_data", "sales.xlsx"))
        assert os.path.isfile(os.path.join(tdir, "gold", "memo.docx"))
        assert os.path.isfile(os.path.join(tdir, "gold_alt", "report.json"))
        assert not os.path.exists(os.path.join(tdir, "references", "01_brief", "Client_Email.docx"))
        with open(os.path.join(out, "gdpval_tasks.jsonl")) as f:
            rows = [json.loads(line) for line in f]
        assert [r["task_id"] for r in rows] == ["ME-Z-99-P01", "ME-Z-99-P02"]
        assert set(rows[0]) == {
            "task_id",
            "sector",
            "occupation",
            "prompt",
            "reference_files",
            "deliverable_files",
            "rubric_pretty",
            "rubric_json",
            "input_dir",
            "task_dir",
        }
        assert rows[0]["deliverable_files"] == ["gold/memo.docx", "gold/model.xlsx"]

    def test_split_mode_export(self, review_bundle: str, pack_bundle: str, tmp_path) -> None:
        result = run_export(
            bundle_dir=review_bundle,
            exports_root=str(tmp_path / "exports"),
            mode="split",
            category_from=pack_bundle,
        )
        m = result.manifest
        assert m["tasks"] == 3  # P01 -> 2 children, P02 passes through
        split = m["split"]
        assert split == {
            "source_tasks": 2,
            "tasks_out": 3,
            "passed_through_unsplit": 1,
            "children": 2,
            "interdependent_flagged": 2,
        }
        assert os.path.isdir(os.path.join(result.out_dir, "ME-Z-99-P01-D01"))
        assert os.path.isdir(os.path.join(result.out_dir, "ME-Z-99-P02"))


def test_cli_convert_runs_acceptance(review_bundle: str, pack_bundle: str, tmp_path) -> None:
    rc = main(
        [
            "convert",
            review_bundle,
            "-o",
            str(tmp_path / "exports"),
            "--category-from",
            pack_bundle,
            "--config",
            str(tmp_path / "missing.yaml"),  # unconfigured bundle: observe only
        ]
    )
    assert rc == 0
