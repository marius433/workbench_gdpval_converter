"""Deterministic validation of generated candidates. No LLM.

A candidate survives only if every check verifies against the actual
environment files:

- every FactRef resolves (cell readable / quote present),
- every ``json_value`` check's derivation — a pure arithmetic expression over
  resolved cell values and numeric literals — recomputes to ``expected``
  within ``tolerance``,
- the candidate clears minimum-shape requirements (enough checks, at least
  one derived check, deliverables named).

Anything else is rejected with reasons recorded; rejected candidates are kept
in the report for review, never silently dropped.
"""

from __future__ import annotations

import ast
import json
import operator
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .facts import ResolvedFact, resolve_fact
from .schema import CandidateEnv, CheckSpec

# Shape requirements for a candidate to count as a real RL environment.
MIN_CHECKS = 3
MIN_DERIVED_CHECKS = 1

_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval(expression: str, names: dict[str, float]) -> float:
    """Evaluate a pure arithmetic expression (+ - * / ** and parentheses)
    over numeric literals and bound names. Anything else raises ValueError."""

    def ev(node: ast.expr) -> float:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ValueError(f"non-numeric literal {node.value!r}")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"unbound name {node.id!r}")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](ev(node.operand))
        raise ValueError(f"disallowed syntax: {ast.dump(node)[:60]}")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"unparseable derivation: {e}") from e
    return ev(tree.body)


@dataclass
class CheckValidation:
    key: str
    ok: bool
    reasons: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    computed: float | None = None


def validate_check(check: CheckSpec, refdir: str) -> CheckValidation:
    """Validate one check against the reference directory."""
    v = CheckValidation(key=check.key, ok=True)
    if check.check_type == "file_exists":
        # Nothing to verify against the environment: the target is an output.
        if not check.key:
            v.ok = False
            v.reasons.append("file_exists check without a target filename")
        return v
    if check.check_type != "json_value":
        v.ok = False
        v.reasons.append(f"unknown check_type {check.check_type!r}")
        return v

    resolved: dict[str, ResolvedFact] = {}
    for ref in check.refs:
        r = resolve_fact(ref, refdir)
        resolved[ref.name] = r
        v.facts.append(f"{ref.name}: {r.detail}")
        if not r.ok:
            v.ok = False
            v.reasons.append(f"fact {ref.name!r} failed: {r.detail}")
    if not check.refs:
        v.ok = False
        v.reasons.append("json_value check has no grounding refs")

    if isinstance(check.expected, bool) or not isinstance(check.expected, int | float):
        # Non-numeric expected values are grounded by their refs only.
        if check.derivation:
            v.ok = False
            v.reasons.append("derivation given for a non-numeric expected value")
        return v

    if not check.derivation:
        v.ok = False
        v.reasons.append("numeric check without a derivation; asserted facts are not trusted")
        return v
    numeric_names = {
        name: float(r.value)
        for name, r in resolved.items()
        if r.ok and isinstance(r.value, int | float) and not isinstance(r.value, bool)
    }
    try:
        computed = safe_eval(check.derivation, numeric_names)
    except (ValueError, ZeroDivisionError) as e:
        v.ok = False
        v.reasons.append(f"derivation failed: {e}")
        return v
    v.computed = computed
    if abs(computed - float(check.expected)) > check.tolerance:
        v.ok = False
        v.reasons.append(
            f"derivation computes {computed:.6g}, expected {check.expected} "
            f"± {check.tolerance}"
        )
    return v


def validate_candidate(candidate: CandidateEnv, refdir: str) -> dict[str, Any]:
    """Validate a candidate; returns the validation record (also stored on the
    candidate). ``valid`` is the gate the exporter respects."""
    check_results = [validate_check(c, refdir) for c in candidate.checks]
    reasons: list[str] = []
    if len(candidate.checks) < MIN_CHECKS:
        reasons.append(f"only {len(candidate.checks)} checks; minimum {MIN_CHECKS}")
    derived = sum(1 for c in candidate.checks if c.derivation)
    if derived < MIN_DERIVED_CHECKS:
        reasons.append(
            f"only {derived} derived check(s); minimum {MIN_DERIVED_CHECKS} — "
            "lookup-only rewards are too easy"
        )
    if not candidate.deliverables:
        reasons.append("no deliverables named")
    failed = [r for r in check_results if not r.ok]
    if failed:
        reasons.append(f"{len(failed)}/{len(check_results)} checks failed verification")
    record: dict[str, Any] = {
        "valid": not reasons,
        "reasons": reasons,
        "checks": [
            {
                "key": r.key,
                "ok": r.ok,
                "computed": r.computed,
                "reasons": r.reasons,
                "facts": r.facts,
            }
            for r in check_results
        ],
    }
    candidate.validation = record
    return record


def validate_dir(candidates_dir: str, refdir: str | None = None) -> dict[str, Any]:
    """Validate a generation output directory (``candidates.json`` +
    ``generation_meta.json``). Writes ``validated.json`` (valid candidates,
    consumable by the exporter) and ``validation_report.json`` (everything,
    with reasons). Returns a summary dict."""
    with open(os.path.join(candidates_dir, "candidates.json")) as f:
        candidates = [CandidateEnv.from_dict(d) for d in json.load(f)]
    if refdir is None:
        with open(os.path.join(candidates_dir, "generation_meta.json")) as f:
            refdir = str(json.load(f)["refdir"])
    assert refdir is not None
    for c in candidates:
        validate_candidate(c, refdir)
    valid = [c for c in candidates if c.validation and c.validation["valid"]]
    with open(os.path.join(candidates_dir, "validated.json"), "w") as f:
        json.dump([c.to_dict() for c in valid], f, indent=1)
    with open(os.path.join(candidates_dir, "validation_report.json"), "w") as f:
        json.dump([c.to_dict() for c in candidates], f, indent=1)
    return {
        "refdir": refdir,
        "candidates": len(candidates),
        "valid": len(valid),
        "rejected": len(candidates) - len(valid),
    }
