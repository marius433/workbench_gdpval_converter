"""Mode C2 tests: fact resolution, validation, generation parsing, export.

The generator is tested with a fake client (no network); the validator and
exporter are fully deterministic and tested for real against the synthetic
bundle's spreadsheet.
"""

from __future__ import annotations

import json
import os

import pytest

from wb2gdpval.rlgen.export_inspect import export_env
from wb2gdpval.rlgen.facts import resolve_fact
from wb2gdpval.rlgen.generate import generate_candidates
from wb2gdpval.rlgen.llm import extract_json
from wb2gdpval.rlgen.schema import CandidateEnv, CheckSpec, DecoySpec, FactRef
from wb2gdpval.rlgen.validate import (
    safe_eval,
    validate_candidate,
    validate_check,
    validate_dir,
)


@pytest.fixture()
def refdir(review_bundle: str) -> str:
    return os.path.join(review_bundle, "ME-Z-99-P01_vF", "reference")


def _growth_check(
    expected: float = 50.0,
    derivation: str = "(q2 - q1) / q1 * 100",
    decoy_derivation: str | None = "q3 / q1 * 100",  # naive path: 60, outside ±0.1
) -> CheckSpec:
    decoy = (
        DecoySpec(
            description="uses the Q3 figure the anomaly invalidates",
            derivation=decoy_derivation,
            refs=[FactRef(name="q3", file="02_data/sales.xlsx", sheet="Data", cell="B4")],
        )
        if decoy_derivation
        else None
    )
    return CheckSpec(
        key="growth_pct",
        description="Q1->Q2 growth",
        report_ask="State the quarter-on-quarter sales growth.",
        unit="pct",
        expected=expected,
        tolerance=0.1,
        derivation=derivation,
        refs=[
            FactRef(name="q1", file="02_data/sales.xlsx", sheet="Data", cell="B2"),
            FactRef(name="q2", file="02_data/sales.xlsx", sheet="Data", cell="B3"),
        ],
        decoy=decoy,
    )


def _candidate(checks: list[CheckSpec]) -> CandidateEnv:
    return CandidateEnv(
        env_id="ME-Z-99-P01-RL-01-test",
        source_task_id="ME-Z-99-P01",
        occupation="Financial and Investment Analysts",
        sector="Finance and Insurance",
        title="Test env",
        prompt="Compute the growth and write growth_memo.docx plus report.json.",
        deliverables=["growth_memo.docx"],
        checks=checks,
    )


class TestSafeEval:
    def test_arithmetic(self) -> None:
        assert safe_eval("(a + b) / c * 100", {"a": 1, "b": 2, "c": 6}) == 50.0
        assert safe_eval("-a + 2**3", {"a": 3}) == 5.0

    def test_rejects_calls_and_unbound_names(self) -> None:
        with pytest.raises(ValueError):
            safe_eval("__import__('os').system('true')", {})
        with pytest.raises(ValueError):
            safe_eval("a + b", {"a": 1.0})
        with pytest.raises(ValueError):
            safe_eval("'x' * 3", {})


class TestFactResolution:
    def test_cell_ref_resolves(self, refdir: str) -> None:
        r = resolve_fact(
            FactRef(name="q1", file="02_data/sales.xlsx", sheet="Data", cell="B2"), refdir
        )
        assert r.ok and r.value == 100

    def test_quote_in_xlsx_and_docx(self, refdir: str) -> None:
        ok = resolve_fact(
            FactRef(name="note", file="02_data/sales.xlsx", quote="warehouse outage"), refdir
        )
        assert ok.ok
        missing = resolve_fact(
            FactRef(name="x", file="02_data/sales.xlsx", quote="not in the file"), refdir
        )
        assert not missing.ok

    def test_references_prefix_is_normalised(self, refdir: str) -> None:
        # Models often write paths with the exported references/ (or sandbox
        # reference/) prefix; both must resolve to the same file.
        for prefixed in ("references/02_data/sales.xlsx", "reference/02_data/sales.xlsx"):
            r = resolve_fact(FactRef(name="q1", file=prefixed, sheet="Data", cell="B2"), refdir)
            assert r.ok and r.value == 100, r.detail

    def test_path_traversal_is_refused(self, refdir: str) -> None:
        r = resolve_fact(FactRef(name="x", file="../grader/grading.yaml", quote="rubric"), refdir)
        assert not r.ok
        assert "escapes" in r.detail

    def test_bad_refs_fail_with_detail(self, refdir: str) -> None:
        assert not resolve_fact(
            FactRef(name="x", file="nope.xlsx", sheet="D", cell="A1"), refdir
        ).ok
        assert not resolve_fact(
            FactRef(name="x", file="02_data/sales.xlsx", sheet="Wrong", cell="A1"), refdir
        ).ok
        assert not resolve_fact(
            FactRef(name="x", file="02_data/sales.xlsx", sheet="Data", cell="Z99"), refdir
        ).ok
        assert not resolve_fact(FactRef(name="x", file="02_data/sales.xlsx"), refdir).ok


class TestValidation:
    def test_valid_candidate_passes(self, refdir: str) -> None:
        cand = _candidate(
            [
                _growth_check(),
                CheckSpec(
                    key="q3_driver_named",
                    description="anomaly driver grounded in the workbook",
                    report_ask="Name the driver of the Q3 result.",
                    expected="warehouse outage",
                    refs=[FactRef(name="d", file="02_data/sales.xlsx", quote="warehouse outage")],
                ),
                CheckSpec(key="growth_memo.docx", description="", check_type="file_exists"),
            ]
        )
        record = validate_candidate(cand, refdir)
        assert record["valid"], record
        assert cand.validation is record

    def test_wrong_expected_value_is_rejected(self, refdir: str) -> None:
        v = validate_check(_growth_check(expected=60.0), refdir)
        assert not v.ok
        assert v.computed == 50.0

    def test_numeric_check_without_derivation_is_rejected(self, refdir: str) -> None:
        c = _growth_check()
        c.derivation = None
        v = validate_check(c, refdir)
        assert not v.ok
        assert any("not trusted" in r for r in v.reasons)

    def test_unverifiable_fact_rejects_candidate(self, refdir: str) -> None:
        bad = _growth_check()
        bad.refs[0] = FactRef(name="q1", file="02_data/sales.xlsx", sheet="Data", cell="Z99")
        cand = _candidate(
            [
                bad,
                _growth_check(),
                CheckSpec(key="growth_memo.docx", description="", check_type="file_exists"),
            ]
        )
        record = validate_candidate(cand, refdir)
        assert not record["valid"]

    def test_shape_requirements(self, refdir: str) -> None:
        record = validate_candidate(_candidate([_growth_check()]), refdir)
        assert not record["valid"]
        assert any("minimum" in r for r in record["reasons"])

    def test_decoy_inside_tolerance_rejects_check(self, refdir: str) -> None:
        # A "decoy" that computes the correct answer is not a trap.
        c = _growth_check(decoy_derivation="(q2 - q1) / q1 * 100")
        v = validate_check(c, refdir)
        assert not v.ok
        assert any("not a real trap" in r for r in v.reasons)

    def test_unresolvable_decoy_fact_rejects_check(self, refdir: str) -> None:
        c = _growth_check()
        assert c.decoy is not None
        c.decoy.refs[0] = FactRef(name="q3", file="02_data/sales.xlsx", sheet="Data", cell="Z99")
        v = validate_check(c, refdir)
        assert not v.ok

    def test_candidate_without_decoys_is_rejected(self, refdir: str) -> None:
        cand = _candidate(
            [
                _growth_check(decoy_derivation=None),
                _growth_check(decoy_derivation=None),
                CheckSpec(key="growth_memo.docx", description="", check_type="file_exists"),
            ]
        )
        record = validate_candidate(cand, refdir)
        assert not record["valid"]
        assert any("decoy" in r for r in record["reasons"])

    def test_missing_report_ask_is_rejected(self, refdir: str) -> None:
        c = _growth_check()
        c.report_ask = ""
        cand = _candidate(
            [c, CheckSpec(key="growth_memo.docx", description="", check_type="file_exists")]
        )
        record = validate_candidate(cand, refdir)
        assert any("report_ask" in r for r in record["reasons"])


class FakeClient:
    """Stands in for LLMClient: returns a canned completion, no network."""

    class _Cfg:
        model = "fake-model"
        base_url = None

    config = _Cfg()

    def __init__(self, completion: str) -> None:
        self._completion = completion

    def complete(self, system: str, user: str) -> str:
        assert "SOURCE TASK ME-Z-99-P01" in user
        assert "02_data/sales.xlsx" in user  # reference contents shown to the model
        return self._completion


def _canned_completion() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "env_id": "growth bridge!",
                    "title": "Growth bridge",
                    "prompt": "Quantify Q1->Q2 growth; write growth_memo.docx and report.json.",
                    "deliverables": ["growth_memo.docx"],
                    "difficulty_rationale": "multi-cell derivation",
                    "checks": [
                        {
                            "key": "growth_pct",
                            "description": "growth",
                            "report_ask": "State the sales growth between the first two quarters.",
                            "unit": "pct",
                            "check_type": "json_value",
                            "expected": 50.0,
                            "tolerance": 0.1,
                            "derivation": "(q2 - q1) / q1 * 100",
                            "decoy": {
                                "description": "naive path via Q3",
                                "derivation": "q3 / q1 * 100",
                                "refs": [
                                    {
                                        "name": "q3",
                                        "file": "02_data/sales.xlsx",
                                        "sheet": "Data",
                                        "cell": "B4",
                                    }
                                ],
                            },
                            "refs": [
                                {
                                    "name": "q1",
                                    "file": "02_data/sales.xlsx",
                                    "sheet": "Data",
                                    "cell": "B2",
                                },
                                {
                                    "name": "q2",
                                    "file": "02_data/sales.xlsx",
                                    "sheet": "Data",
                                    "cell": "B3",
                                },
                            ],
                        },
                        {
                            "key": "q3_driver",
                            "description": "driver",
                            "report_ask": "Name the driver of the Q3 result.",
                            "check_type": "json_value",
                            "expected": "warehouse outage",
                            "refs": [
                                {
                                    "name": "d",
                                    "file": "02_data/sales.xlsx",
                                    "quote": "warehouse outage",
                                }
                            ],
                        },
                        {
                            "key": "growth_memo.docx",
                            "check_type": "file_exists",
                            "description": "exists",
                        },
                    ],
                }
            ]
        }
    )


class TestGenerateAndExport:
    def test_pipeline_generate_validate_export(
        self, review_bundle: str, pack_bundle: str, tmp_path
    ) -> None:
        task_dir = os.path.join(review_bundle, "ME-Z-99-P01_vF")
        out = str(tmp_path / "rl_out")
        result = generate_candidates(
            task_dir, out, n=1, client=FakeClient(_canned_completion()), category_from=pack_bundle
        )
        cdir = os.path.join(out, "ME-Z-99-P01")
        assert os.path.isfile(os.path.join(cdir, "candidates.json"))
        assert os.path.isfile(os.path.join(cdir, "generation_meta.json"))
        assert result.candidates[0].env_id == "ME-Z-99-P01-RL-01-growth-bridge"

        summary = validate_dir(cdir)
        assert summary == {
            "refdir": os.path.abspath(os.path.join(task_dir, "reference")),
            "candidates": 1,
            "valid": 1,
            "rejected": 0,
        }

        with open(os.path.join(cdir, "validated.json")) as f:
            cand = CandidateEnv.from_dict(json.load(f)[0])
        env_dir = export_env(cand, result.refdir, str(tmp_path / "envs"))
        for name in ("task.py", "Dockerfile", "compose.yaml", "checks.json", "env.json"):
            assert os.path.isfile(os.path.join(env_dir, name))
        assert os.path.isfile(os.path.join(env_dir, "references", "02_data", "sales.xlsx"))
        with open(os.path.join(env_dir, "env.json")) as f:
            env = json.load(f)
        assert "report.json" in env["prompt_with_addendum"]
        # Keys are blinded: the agent sees result_NN and the report_ask wording,
        # never the descriptive internal key.
        addendum = env["prompt_with_addendum"]
        assert "`result_01`" in addendum and "`result_02`" in addendum
        assert "growth_pct" not in addendum
        assert "State the sales growth between the first two quarters." in addendum
        assert "(unit: pct)" in addendum
        with open(os.path.join(env_dir, "checks.json")) as f:
            checks = json.load(f)
        assert [c.get("blinded_key") for c in checks] == ["result_01", "result_02", None]
        # generated task.py must at least be valid Python
        with open(os.path.join(env_dir, "task.py")) as f:
            compile(f.read(), "task.py", "exec")

    def test_export_refuses_unvalidated_candidate(self, refdir: str, tmp_path) -> None:
        cand = _candidate([_growth_check()])
        with pytest.raises(ValueError, match="not passed validation"):
            export_env(cand, refdir, str(tmp_path / "envs"))


def test_extract_json_variants() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Here you go:\n```json\n{"a": 1}\n```\nDone.') == {"a": 1}
    assert extract_json('preamble {"a": {"b": 2}} trailing') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        extract_json("no json here")
