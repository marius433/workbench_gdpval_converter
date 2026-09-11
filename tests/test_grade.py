"""Grade module tests: extraction, pairwise judging (fake client), BT/Elo fit.

The worker runner is a thin inspect-ai/Docker wrapper and is exercised by the
live pilots, not unit tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wb2gdpval.export import run_export
from wb2gdpval.grade.elo import bradley_terry_elo, load_judgments, per_task_vs_gold
from wb2gdpval.grade.extract import submission_text
from wb2gdpval.grade.judge import judge_export
from wb2gdpval.occupations import OccupationResolver  # noqa: F401  (fixture bundles)


def _judgment(task: str, a: str, b: str, winner: str, swapped: bool = False) -> dict[str, object]:
    return {
        "task_id": task,
        "player_a": a,
        "player_b": b,
        "winner": winner,
        "swapped": swapped,
        "judge_model": "fake",
    }


class TestElo:
    def test_gold_anchor_and_ordering(self) -> None:
        # gold beats both models everywhere; model1 beats model2.
        js = []
        for t in ("T1", "T2", "T3", "T4"):
            js += [
                _judgment(t, "m1", "gold", "gold"),
                _judgment(t, "m1", "gold", "gold", swapped=True),
                _judgment(t, "m2", "gold", "gold"),
                _judgment(t, "m2", "gold", "gold", swapped=True),
                _judgment(t, "m1", "m2", "m1"),
                _judgment(t, "m1", "m2", "m1", swapped=True),
            ]
        ratings = bradley_terry_elo(js)
        assert ratings["gold"] == pytest.approx(1000.0)
        assert ratings["gold"] > ratings["m1"] > ratings["m2"]

    def test_ties_count_half(self) -> None:
        js = [
            _judgment("T1", "m1", "gold", "tie"),
            _judgment("T1", "m1", "gold", "tie", swapped=True),
        ]
        table = per_task_vs_gold(js)
        assert table["T1"]["m1"]["rate"] == pytest.approx(0.5)

    def test_per_task_rates(self) -> None:
        js = [
            _judgment("T1", "m1", "gold", "gold"),
            _judgment("T1", "m1", "gold", "m1", swapped=True),
            _judgment("T2", "m1", "gold", "gold"),
            _judgment("T2", "m1", "gold", "gold", swapped=True),
        ]
        table = per_task_vs_gold(js)
        assert table["T1"]["m1"]["rate"] == pytest.approx(0.5)
        assert table["T2"]["m1"]["rate"] == pytest.approx(0.0)


class FakeJudge:
    """Prefers the submission whose text contains 'GOLDEN'; ties otherwise."""

    class _Cfg:
        model = "fake-judge"
        base_url = None

    config = _Cfg()

    def complete(self, system: str, user: str) -> str:
        a = user.split("SUBMISSION A:\n", 1)[1].split("SUBMISSION B:\n")[0]
        b = user.split("SUBMISSION B:\n", 1)[1]
        if "Growth was 50 percent" in a and "Growth was 50 percent" not in b:
            return '{"winner": "A", "reasoning": "A carries the correct figure."}'
        if "Growth was 50 percent" in b and "Growth was 50 percent" not in a:
            return '{"winner": "B", "reasoning": "B carries the correct figure."}'
        return '{"winner": "tie", "reasoning": "indistinguishable"}'


@pytest.fixture()
def graded_export(review_bundle: str, pack_bundle: str, tmp_path: Path) -> str:
    """A written export with one fake worker's runs alongside gold."""
    result = run_export(
        bundle_dir=review_bundle,
        exports_root=str(tmp_path / "exports"),
        category_from=pack_bundle,
    )
    export = result.out_dir
    # Fake worker deliverables: wrong figure, so gold should win the memo tasks.
    for task_id, files in {
        "ME-Z-99-P01": {"memo.docx": "Growth was 12 percent.", "model.xlsx": "n/a"},
        "ME-Z-99-P02": {"analysis.docx": "The Q3 dip is seasonal."},
    }.items():
        d = os.path.join(export, "runs", "openrouter_worker-x", task_id)
        os.makedirs(d)
        for name, content in files.items():
            # plain-text stand-ins; extraction falls back to '(binary ...)' for
            # unreadable office files, symmetrically for both sides
            with open(
                os.path.join(d, name.replace(".docx", ".md").replace(".xlsx", ".csv")), "w"
            ) as f:
                f.write(content)
    return export


def test_judge_export_and_elo(graded_export: str) -> None:
    summary = judge_export(graded_export, FakeJudge(), workers=["openrouter/worker-x"])  # type: ignore[arg-type]
    assert summary["errors"] == 0
    assert summary["new"] == 4  # 2 tasks x 1 pair x both orders

    # resumable: nothing new on a second pass
    summary2 = judge_export(graded_export, FakeJudge(), workers=["openrouter/worker-x"])  # type: ignore[arg-type]
    assert summary2["new"] == 0 and summary2["skipped"] == 4

    judgments = load_judgments(graded_export)
    assert len(judgments) == 4
    ratings = bradley_terry_elo(judgments)
    assert ratings["gold"] == pytest.approx(1000.0)
    # gold's memo carries the correct figure, so gold must not be below the worker
    assert ratings["gold"] >= ratings["openrouter/worker-x"]


def test_sparse_pairs_connected_and_small() -> None:
    from wb2gdpval.grade.judge import _select_pairs

    workers = ["w1", "w2", "w3", "w4"]
    players = workers + ["gold"]
    pairs = _select_pairs(players, workers, "sparse")
    # every worker vs gold + a ring over workers
    assert ("w1", "gold") in pairs and ("w4", "gold") in pairs
    assert len(pairs) == 8  # 4 gold pairs + 4 ring pairs (vs C(5,2)=10 full)
    assert _select_pairs(["w1", "w2", "gold"], ["w1", "w2"], "sparse") == [
        ("w1", "gold"),
        ("w2", "gold"),
        ("w1", "w2"),
    ]
    with pytest.raises(ValueError):
        _select_pairs(players, workers, "random")


def test_judge_skips_players_without_submission(graded_export: str) -> None:
    # 'ghost' has no runs dir: no pair involving it may be judged, and the
    # remaining players are still judged normally.
    summary = judge_export(
        graded_export,
        FakeJudge(),  # type: ignore[arg-type]
        workers=["openrouter/worker-x", "ghost"],
        pairs="sparse",
    )
    assert summary["errors"] == 0
    with open(os.path.join(graded_export, "judgments.jsonl")) as f:
        rows = [json.loads(line) for line in f]
    assert rows and not any("ghost" in (r["player_a"], r["player_b"]) for r in rows)


def test_submission_text_excludes_report_json(tmp_path: Path) -> None:
    d = tmp_path / "sub"
    d.mkdir()
    (d / "memo.md").write_text("The finding.")
    (d / "report.json").write_text(json.dumps({"x": 1}))
    text = submission_text(str(d))
    assert "The finding." in text
    assert "report.json" not in text
    assert submission_text(str(tmp_path / "missing")) == "(no submission)"
