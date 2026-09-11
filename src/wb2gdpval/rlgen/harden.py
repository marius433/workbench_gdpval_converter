"""Adversarial hardening: regenerate envs the attacker solved.

For each exported env whose gate result says *solved*, the generator model is
shown its own candidate, the attacker's per-check results, and the full data
room again, and asked for ONE revised candidate on which the demonstrated
solution path fails. The revision goes through the same validate → export
chain (using the env's own ``references/`` copy as ground truth) and gets a
``-hN`` id suffix plus ``harden_round`` provenance. Iterate by running gate
and harden alternately until the attacker stops solving first try.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .difficulty import attack_summary
from .export_inspect import export_env
from .generate import build_context, parse_candidates
from .llm import LLMClient
from .schema import CANDIDATE_JSON_GUIDE, CandidateEnv
from .validate import validate_candidate

_SYSTEM = (
    """You harden benchmark tasks for AI agents. You previously generated an RL
environment (an assignment over a fixed data room, rewarded by machine-checkable
facts). A strong model then solved it on its first attempt — the task is too easy
and will be discarded unless you make the demonstrated solution path fail.

You will be given the data room, the previous candidate (including its hidden
checks), and the attack result showing what the attacker reported per check.

Produce ONE revised candidate, same JSON shape as before. Requirements:
- Keep it grounded in the same data room, verifiable by the same validator: every
  numeric check needs a derivation over refs the validator can resolve, at least two
  checks need a verified decoy, and every check needs a method-free report_ask.
- Change what is asked, not just the wording: target quantities whose obvious
  computation (the one the attacker just demonstrated) lands outside tolerance —
  deeper joins, basis corrections the room only implies, quantities conditional on
  facts stated in prose the attacker ignored.
- Tighten tolerances to rounding only.
- The prompt and report_asks must not hint at the traps.

Return ONLY a JSON object shaped as:
"""
    + CANDIDATE_JSON_GUIDE
)


@dataclass
class HardenResult:
    env_id: str
    revised_env_id: str | None = None
    exported_to: str | None = None
    valid: bool = False
    reasons: list[str] = field(default_factory=list)


def _feedback_message(base_context: str, candidate: dict[str, object], attack: str) -> str:
    return (
        base_context
        + "\n\nPREVIOUS CANDIDATE (yours; the agent never saw keys/derivations/decoys):\n"
        + json.dumps(candidate, indent=1)
        + "\n\nATTACK RESULT (first attempt):\n"
        + attack
        + "\n\nProduce the single revised candidate now. JSON only."
    )


def harden_env(
    env_dir: str,
    source_task_dir: str,
    client: LLMClient | None = None,
    category_from: str | None = None,
) -> HardenResult:
    """Regenerate one solved env; validate and export the revision."""
    with open(os.path.join(env_dir, "env.json")) as f:
        env = json.load(f)
    prev = CandidateEnv.from_dict(env)
    result = HardenResult(env_id=prev.env_id)

    # The env's own references/ copy is the validation ground truth — the
    # revision must verify against exactly what the agent will be given.
    refdir = os.path.join(env_dir, "references")

    _tid, base_context, _meta = build_context(source_task_dir, n=1, category_from=category_from)
    prev_dict = prev.to_dict()
    prev_dict.pop("validation", None)
    user = _feedback_message(base_context, prev_dict, attack_summary(env_dir))

    client = client or LLMClient()
    completion = client.complete(_SYSTEM, user)
    with open(os.path.join(env_dir, "harden_completion.txt"), "w") as f:
        f.write(completion)
    revised = parse_candidates(completion, prev.source_task_id, prev.occupation, prev.sector)
    if not revised:
        result.reasons.append("no candidate in completion")
        return result
    cand = revised[0]
    cand.harden_round = prev.harden_round + 1
    cand.env_id = f"{prev.env_id}-h{cand.harden_round}"
    result.revised_env_id = cand.env_id

    record = validate_candidate(cand, refdir)
    result.valid = bool(record["valid"])
    if not result.valid:
        result.reasons.extend(str(r) for r in record["reasons"])
        with open(os.path.join(env_dir, "harden_rejected.json"), "w") as f:
            json.dump(cand.to_dict(), f, indent=1)
        return result

    envs_root = os.path.dirname(os.path.normpath(env_dir))
    result.exported_to = export_env(cand, refdir, envs_root)
    return result


def harden_all(
    envs_root: str,
    rl_out: str,
    client: LLMClient | None = None,
    category_from: str | None = None,
) -> list[HardenResult]:
    """Harden every env under ``envs_root`` whose gate result says solved.

    ``rl_out`` locates each source task's ``generation_meta.json`` (for the
    original task dir whose reference dumps feed the regeneration context).
    Envs without a gate result are skipped — gate first.
    """
    results: list[HardenResult] = []
    client = client or LLMClient()
    for name in sorted(os.listdir(envs_root)):
        env_dir = os.path.join(envs_root, name)
        gate_path = os.path.join(env_dir, "gate_result.json")
        if not os.path.isfile(gate_path):
            continue
        with open(gate_path) as f:
            gate = json.load(f)
        if not gate.get("solved"):
            continue
        with open(os.path.join(env_dir, "env.json")) as f:
            source_task_id = json.load(f).get("source_task_id", "")
        meta_path = os.path.join(rl_out, source_task_id, "generation_meta.json")
        if not os.path.isfile(meta_path):
            results.append(
                HardenResult(env_id=name, reasons=[f"no generation_meta.json at {meta_path}"])
            )
            continue
        with open(meta_path) as f:
            source_task_dir = json.load(f)["source_task_dir"]
        results.append(
            harden_env(env_dir, source_task_dir, client=client, category_from=category_from)
        )
    with open(os.path.join(envs_root, "harden_report.json"), "w") as f:
        json.dump([r.__dict__ for r in results], f, indent=1)
    return results
