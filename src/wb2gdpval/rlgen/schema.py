"""Candidate RL-environment schema shared by generator, validator and exporter.

A candidate's reward is a list of ``CheckSpec``s. Every numeric check carries
a ``derivation`` — an arithmetic expression over named ``FactRef``s — so the
validator can recompute the expected value from the actual environment files.
A check whose facts cannot be verified is rejected; a candidate with a
rejected check is rejected whole. Never trust an asserted fact.

Difficulty machinery (added after the first gate run, where a strong model
solved 10/10 generated envs first try):

- ``report_ask`` / ``unit`` — the agent-facing wording for a check. The
  internal ``key`` (descriptive, e.g. ``net_liability_approved_breakage``)
  is never shown to the agent: the exporter blinds it to ``result_NN`` so
  key names cannot telegraph the method or the basis resolution.
- ``decoy`` — the naive derivation a competent-but-unwary analyst would
  follow (the published column, the headline figure). The validator computes
  it from the real files and requires it to land OUTSIDE the check's
  tolerance, proving the task punishes the obvious path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class FactRef:
    """A pointer into the environment's reference files.

    Exactly one addressing style applies:
    - spreadsheet cell: ``file`` + ``sheet`` + ``cell`` (value read directly)
    - text evidence: ``file`` + ``quote`` (exact substring of extracted text)

    ``name`` binds the resolved (numeric) value for use in a derivation.
    """

    name: str
    file: str
    sheet: str | None = None
    cell: str | None = None
    quote: str | None = None


@dataclass
class DecoySpec:
    """The trap: the derivation the obvious-but-wrong path yields.

    Validated against the real files like any derivation; a decoy that lands
    inside the check's tolerance disqualifies the check (the trap isn't one).
    """

    description: str
    derivation: str
    refs: list[FactRef] = field(default_factory=list)


@dataclass
class CheckSpec:
    """One programmatic reward check, evaluated against the agent's report.json.

    ``check_type``:
    - ``json_value`` — report.json key must equal ``expected`` within
      ``tolerance``; ``derivation`` + ``refs`` prove the value from the files.
    - ``file_exists`` — the deliverable file named by ``key`` must exist.
    """

    key: str
    description: str
    check_type: str = "json_value"
    expected: float | str | bool | None = None
    tolerance: float = 0.0
    weight: float = 1.0
    derivation: str | None = None
    refs: list[FactRef] = field(default_factory=list)
    # Agent-facing wording: defines WHAT to report without disclosing HOW.
    report_ask: str = ""
    unit: str = ""
    decoy: DecoySpec | None = None
    # Set by the exporter: the key the agent actually reports under.
    blinded_key: str | None = None


def _fact_refs(raw: list[dict[str, Any]]) -> list[FactRef]:
    return [
        FactRef(
            name=r["name"],
            file=r["file"],
            sheet=r.get("sheet"),
            cell=r.get("cell"),
            quote=r.get("quote"),
        )
        for r in raw
    ]


@dataclass
class CandidateEnv:
    """One generated environment: assignment prompt + programmatic reward."""

    env_id: str
    source_task_id: str
    occupation: str
    sector: str
    title: str
    prompt: str
    deliverables: list[str]
    checks: list[CheckSpec]
    difficulty_rationale: str = ""
    validation: dict[str, Any] | None = None
    # Adversarial-hardening provenance: how many feedback rounds produced this.
    harden_round: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CandidateEnv:
        checks = []
        for c in d.get("checks", []):
            decoy_raw = c.get("decoy")
            decoy = (
                DecoySpec(
                    description=decoy_raw.get("description", ""),
                    derivation=decoy_raw.get("derivation", ""),
                    refs=_fact_refs(decoy_raw.get("refs", [])),
                )
                if decoy_raw
                else None
            )
            checks.append(
                CheckSpec(
                    key=c["key"],
                    description=c.get("description", ""),
                    check_type=c.get("check_type", "json_value"),
                    expected=c.get("expected"),
                    tolerance=float(c.get("tolerance", 0.0)),
                    weight=float(c.get("weight", 1.0)),
                    derivation=c.get("derivation"),
                    refs=_fact_refs(c.get("refs", [])),
                    report_ask=c.get("report_ask", ""),
                    unit=c.get("unit", ""),
                    decoy=decoy,
                    blinded_key=c.get("blinded_key"),
                )
            )
        return cls(
            env_id=d["env_id"],
            source_task_id=d.get("source_task_id", ""),
            occupation=d.get("occupation", "UNMAPPED"),
            sector=d.get("sector", "UNMAPPED"),
            title=d.get("title", d["env_id"]),
            prompt=d["prompt"],
            deliverables=list(d.get("deliverables", [])),
            checks=checks,
            difficulty_rationale=d.get("difficulty_rationale", ""),
            validation=d.get("validation"),
            harden_round=int(d.get("harden_round", 0)),
        )


# The JSON shape the generator model is asked to produce, embedded in the
# generation prompt. Kept next to the dataclasses so they cannot drift apart.
CANDIDATE_JSON_GUIDE = """{
  "candidates": [
    {
      "env_id": "<slug, e.g. petronusa-fuel-margin-bridge>",
      "title": "<short title>",
      "prompt": "<the full assignment, self-contained, addressed to the analyst. It must \
define the work and the deliverables, but must NOT name the trap, the correct basis, or \
the adjustment that resolves it>",
      "deliverables": ["<output filename the agent must produce>", "..."],
      "difficulty_rationale": "<the trap this is built on, and why the obvious path fails>",
      "checks": [
        {
          "key": "<internal key, snake_case, descriptive — never shown to the agent>",
          "description": "<what this measures and how it is derived>",
          "report_ask": "<one sentence telling the agent WHAT quantity to report — \
must not hint at the trap, the basis choice, or the adjustment>",
          "unit": "<unit of the reported value, e.g. 'pct', 'SRP bn'>",
          "check_type": "json_value",
          "expected": <number>,
          "tolerance": <number, absolute>,
          "weight": 1.0,
          "derivation": "<arithmetic over ref names, e.g. (a + b) / c * 100>",
          "refs": [
            {"name": "a", "file": "<path under references/>",
             "sheet": "<sheet name>", "cell": "<e.g. D14>"},
            {"name": "c", "file": "<path>", "quote": "<exact substring proving the fact>"}
          ],
          "decoy": {
            "description": "<the obvious-but-wrong path, e.g. lifting the published column>",
            "derivation": "<arithmetic the naive path yields, over decoy ref names>",
            "refs": [{"name": "pub", "file": "<path>", "sheet": "<sheet>", "cell": "<cell>"}]
          }
        },
        {"key": "<deliverable filename>", "check_type": "file_exists",
         "description": "deliverable exists", "weight": 0.5}
      ]
    }
  ]
}"""
