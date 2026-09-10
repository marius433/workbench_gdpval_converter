"""Rubric extraction and harness de-coupling.

GDPval judges rank deliverables; there is no agent transcript and no
report.json. The sentence forms in ``PLUMBING`` are pure WorkBench harness
plumbing and are removed whole. Nothing else is touched: "transcript" also
means *interview* transcript in some bundles (ME-A-01 P06/P07 grade 22 wave-1
interview transcripts), so a blind regex over the word would corrupt live
criteria. Whatever survives is flagged, never edited.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

PLUMBING = [
    r"The environment contains[^.]*?\.[ \t]*\n?",
    r"Inspect the actual[\s]+output and (?:the )?source files, not just the transcript\.[ \t]*\n?",
    r"Do not lower the score because the transcript is missing[^.]*\.[ \t]*\n?",
    r"Do not be swayed by the (?:model|agent)'?s own framing or claims about what it did\.[ \t]*\n?",
    r"The transcript is your source for reasoning quality[^.]*\.[ \t]*\n?",
    r"The agent also writes a[\s]*`?report\.json`?\.[ \t]*\n?",
    r"`?report\.json`? is the agent'?s own index of the positions it took[^.]*\.[ \t]*\n?",
]

GDPVAL_PREAMBLE = (
    "Grading note: rank the candidate deliverables against each other and against the "
    "expert reference, on the deliverable files alone — their content, analysis and "
    "craft. There is no agent transcript and no report.json index available: a "
    "position counts only where it is present and supported in the deliverable "
    "itself.\n\n"
)

RESIDUAL = (
    (
        r"report\.json",
        "cites report.json, which this prompt does not ask the model to produce",
    ),
    (
        r"agent'?s\s+transcript|\bthe transcript\b",
        "still refers to the agent transcript",
    ),
)

Residual = dict[str, str]  # {'why': ..., 'snippet': ...}


def decouple(text: str) -> tuple[str, list[Residual]]:
    """Strip harness plumbing from a rubric. Returns (text, residual_snippets)."""
    if not text:
        return text, []
    out = text
    for pat in PLUMBING:
        out = re.sub(pat, "", out, flags=re.I)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    resid: list[Residual] = []
    for pat, why in RESIDUAL:
        for m in re.finditer(pat, out, re.I):
            a, b = max(0, m.start() - 70), min(len(out), m.end() + 70)
            resid.append({"why": why, "snippet": re.sub(r"\s+", " ", out[a:b]).strip()})
    return out, resid


def decouple_tree(node: Any, resid: list[Residual]) -> Any:
    """Apply decouple() to every string leaf of a rubric_json structure."""
    if isinstance(node, str):
        t, r = decouple(node)
        resid.extend(r)
        return t
    if isinstance(node, list):
        return [decouple_tree(x, resid) for x in node]
    if isinstance(node, dict):
        return {k: decouple_tree(v, resid) for k, v in node.items()}
    return node


@dataclass
class RubricBundle:
    """A task's rubric in both GDPval renderings, plus audit material.

    ``pretty``/``json_text`` are the de-coupled dataset fields;
    ``pretty_source`` is the untouched original kept per task for audit.
    """

    pretty: str = ""
    json_text: str = ""
    pretty_source: str = ""
    residual: list[Residual] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def load_rubrics(grading_path: str) -> RubricBundle:
    """Extract and de-couple the rubric for one task.

    The expert rubric lives in ``grading_prompt.md`` (next to grading.yaml)
    and in grading.yaml's ``llm_judge.rubric``. It ships as dataset metadata
    only — never to the model.
    """
    rb = RubricBundle()
    gp = os.path.join(os.path.dirname(grading_path), "grading_prompt.md")
    if os.path.isfile(gp):
        with open(gp) as f:
            rb.pretty = f.read().strip()
    else:
        rb.flags.append("grading_prompt.md missing; rubric_pretty empty")
    try:
        with open(grading_path) as f:
            grading = yaml.safe_load(f) or {}
        judge_rubric = (grading.get("llm_judge") or {}).get("rubric")
        if judge_rubric is not None:
            rb.json_text = json.dumps(judge_rubric, indent=1)
        else:
            rb.flags.append("llm_judge.rubric missing; rubric_json empty")
    except Exception as e:
        rb.flags.append(f"grading.yaml unreadable for rubric: {type(e).__name__}")

    rb.pretty_source = rb.pretty
    resid: list[Residual] = []
    if rb.pretty:
        rb.pretty, r1 = decouple(rb.pretty)
        rb.pretty = GDPVAL_PREAMBLE + rb.pretty
        resid.extend(r1)
    if rb.json_text:
        r2: list[Residual] = []
        rb.json_text = json.dumps(decouple_tree(json.loads(rb.json_text), r2), indent=1)
        resid.extend(r2)
    seen: set[tuple[str, str]] = set()
    for r in resid:
        key = (r["why"], r["snippet"])
        if key not in seen:
            seen.add(key)
            rb.residual.append(r)
    if rb.residual:
        kinds = sorted({r["why"] for r in rb.residual})
        rb.flags.append(
            "rubric residual coupling after de-coupling ("
            + "; ".join(kinds)
            + "); needs ME review"
        )
    return rb
