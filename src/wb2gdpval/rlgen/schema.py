"""Candidate RL-environment schema shared by generator, validator and exporter.

A candidate's reward is a list of ``CheckSpec``s. Every numeric check carries
a ``derivation`` — an arithmetic expression over named ``FactRef``s — so the
validator can recompute the expected value from the actual environment files.
A check whose facts cannot be verified is rejected; a candidate with a
rejected check is rejected whole. Never trust an asserted fact.
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CandidateEnv:
        checks = [
            CheckSpec(
                key=c["key"],
                description=c.get("description", ""),
                check_type=c.get("check_type", "json_value"),
                expected=c.get("expected"),
                tolerance=float(c.get("tolerance", 0.0)),
                weight=float(c.get("weight", 1.0)),
                derivation=c.get("derivation"),
                refs=[
                    FactRef(
                        name=r["name"],
                        file=r["file"],
                        sheet=r.get("sheet"),
                        cell=r.get("cell"),
                        quote=r.get("quote"),
                    )
                    for r in c.get("refs", [])
                ],
            )
            for c in d.get("checks", [])
        ]
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
        )


# The JSON shape the generator model is asked to produce, embedded in the
# generation prompt. Kept next to the dataclasses so they cannot drift apart.
CANDIDATE_JSON_GUIDE = """{
  "candidates": [
    {
      "env_id": "<slug, e.g. petronusa-fuel-margin-bridge>",
      "title": "<short title>",
      "prompt": "<the full assignment, self-contained, addressed to the analyst>",
      "deliverables": ["<output filename the agent must produce>", "..."],
      "difficulty_rationale": "<why a strong model should NOT solve this in one pass>",
      "checks": [
        {
          "key": "<report.json key, snake_case, units in the name>",
          "description": "<what this measures and how it is derived>",
          "check_type": "json_value",
          "expected": <number>,
          "tolerance": <number, absolute>,
          "weight": 1.0,
          "derivation": "<arithmetic over ref names, e.g. (a + b) / c * 100>",
          "refs": [
            {"name": "a", "file": "<path under references/>",
             "sheet": "<sheet name>", "cell": "<e.g. D14>"},
            {"name": "c", "file": "<path>", "quote": "<exact substring proving the fact>"}
          ]
        },
        {"key": "<deliverable filename>", "check_type": "file_exists",
         "description": "deliverable exists", "weight": 0.5}
      ]
    }
  ]
}"""
