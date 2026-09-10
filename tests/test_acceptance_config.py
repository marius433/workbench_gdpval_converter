"""Bundle config loading and acceptance checks (observe/assert workflow)."""

from __future__ import annotations

import os
import textwrap

from wb2gdpval.acceptance import check_export
from wb2gdpval.config import load_bundle_config
from wb2gdpval.export import run_export


def _write_config(path: str, bundle_name: str, body: str) -> None:
    with open(path, "w") as f:
        f.write(textwrap.dedent(f"bundles:\n  {bundle_name}:\n{body}"))


def test_load_bundle_config_resolves_relative_paths(tmp_path) -> None:
    cfg_path = tmp_path / "bundles.yaml"
    _write_config(
        str(cfg_path),
        "mini_review_bundle",
        "    category_from: ../pack\n"
        "    expected:\n      one2one: { tasks: 2, clean: 0, flagged: 2 }\n"
        "    anchors:\n"
        "      - task_id: ME-Z-99-P01\n"
        "        prompt_contains: ['quarterly memo']\n",
    )
    cfg = load_bundle_config(str(cfg_path), "/anywhere/mini_review_bundle")
    assert cfg is not None
    assert cfg.category_from == os.path.normpath(str(tmp_path / ".." / "pack"))
    assert cfg.expected["one2one"].tasks == 2
    assert cfg.anchors[0].task_id == "ME-Z-99-P01"
    # unknown bundle -> None (observing first run is allowed)
    assert load_bundle_config(str(cfg_path), "/anywhere/other_bundle") is None
    assert load_bundle_config(str(tmp_path / "missing.yaml"), "x") is None


def test_acceptance_observe_then_assert(review_bundle: str, pack_bundle: str, tmp_path) -> None:
    result = run_export(
        bundle_dir=review_bundle,
        exports_root=str(tmp_path / "exports"),
        category_from=pack_bundle,
    )
    cfg_path = str(tmp_path / "bundles.yaml")
    name = os.path.basename(review_bundle)

    # No config: structural checks only, with a note.
    report = check_export(result.out_dir, None, "one2one")
    assert report.ok
    assert any("no config block" in n for n in report.notes)

    # Config without expected counts: observe, don't fail.
    _write_config(cfg_path, name, "    task_glob: 'ME-*'\n")
    report = check_export(result.out_dir, load_bundle_config(cfg_path, review_bundle), "one2one")
    assert report.ok
    assert any("record them" in n for n in report.notes)

    # Recorded counts + anchor: assert and pass.
    _write_config(
        cfg_path,
        name,
        "    expected:\n      one2one: { tasks: 2, clean: 0, flagged: 2 }\n"
        "    anchors:\n"
        "      - task_id: ME-Z-99-P01\n"
        "        prompt_contains: ['quarterly memo', 'the brief below']\n"
        "        prompt_excludes: ['From:']\n"
        "        deliverables: [memo.docx, model.xlsx]\n"
        "        references_exclude: [Client_Email.docx]\n",
    )
    report = check_export(result.out_dir, load_bundle_config(cfg_path, review_bundle), "one2one")
    assert report.ok, report.failures

    # Wrong recorded counts: fail loudly.
    _write_config(
        cfg_path, name, "    expected:\n      one2one: { tasks: 3, clean: 1, flagged: 2 }\n"
    )
    report = check_export(result.out_dir, load_bundle_config(cfg_path, review_bundle), "one2one")
    assert not report.ok
    assert any("counts mismatch" in f for f in report.failures)

    # Anchor assertion failure.
    _write_config(
        cfg_path,
        name,
        "    anchors:\n"
        "      - task_id: ME-Z-99-P01\n"
        "        prompt_contains: ['text that is not there']\n",
    )
    report = check_export(result.out_dir, load_bundle_config(cfg_path, review_bundle), "one2one")
    assert any("prompt lacks" in f for f in report.failures)


def test_acceptance_flags_junk_and_missing_pieces(tmp_path) -> None:
    export = tmp_path / "export"
    tdir = export / "ME-Z-99-P01"
    (tdir / "references").mkdir(parents=True)
    (tdir / "references" / ".DS_Store").write_bytes(b"junk")
    report = check_export(str(export), None, "one2one")
    assert any("junk" in f for f in report.failures)
    assert any("missing task.json" in f for f in report.failures)
    assert any("missing gold/" in f for f in report.failures)
