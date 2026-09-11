"""Difficulty gate: a strong model attempts each exported environment.

Anything solved on the first attempt is discarded as too easy (moved to
``discarded_too_easy/`` next to the envs root, never deleted). This is the
AgentMercury ``DockerWorldsPassAtKValidator`` idea and it is the difference
between generated tasks and generated slop: a reward a frontier model earns
in one pass measures nothing.

Requires the ``inspect`` extra (``uv sync --extra inspect``), Docker, and
model credentials in the environment (inspect-ai reads its own provider env
vars, e.g. ``ANTHROPIC_API_KEY`` / ``OPENROUTER_API_KEY``).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass

# The attacker must clear this fraction of the reward to count as "solved".
DEFAULT_SOLVE_THRESHOLD = 0.8
DEFAULT_ATTACKER = "anthropic/claude-opus-5"


@dataclass
class GateResult:
    env_id: str
    model: str
    score: float | None
    solved: bool
    discarded: bool
    error: str | None = None


def _eval_env(env_dir: str, model: str, log_dir: str) -> float:
    """Run one inspect eval of the environment; returns the mean score."""
    try:
        from inspect_ai import eval as inspect_eval
    except ImportError as e:  # pragma: no cover - import guard
        raise RuntimeError(
            "inspect-ai is required for the difficulty gate: uv sync --extra inspect"
        ) from e
    logs = inspect_eval(
        os.path.join(env_dir, "task.py"),
        model=model,
        log_dir=log_dir,
        display="plain",
    )
    log = logs[0]
    if log.status != "success" or log.results is None:
        raise RuntimeError(f"eval did not complete: status={log.status}")
    for score in log.results.scores:
        metric = score.metrics.get("mean")
        if metric is not None:
            return float(metric.value)
    raise RuntimeError("no mean metric in eval results")


def attack_summary(env_dir: str) -> str:
    """How the attacker did on this env, for adversarial hardening feedback.

    Reads the newest gate log and returns the score plus the scorer's
    per-check explanation (reported vs expected per key). Best effort — an
    unreadable log yields a minimal summary from gate_result.json.
    """
    result_path = os.path.join(env_dir, "gate_result.json")
    lines: list[str] = []
    if os.path.isfile(result_path):
        with open(result_path) as f:
            r = json.load(f)
        lines.append(f"attacker={r.get('model')} score={r.get('score')}")
    log_dir = os.path.join(env_dir, "gate_logs")
    try:
        from inspect_ai.log import read_eval_log

        logs = sorted(f for f in os.listdir(log_dir) if f.endswith(".eval") or f.endswith(".json"))
        log = read_eval_log(os.path.join(log_dir, logs[-1]))
        for sample in log.samples or []:
            for score in (sample.scores or {}).values():
                if score.explanation:
                    lines.append(score.explanation)
    except Exception as e:
        lines.append(f"(gate log unreadable: {type(e).__name__})")
    return "\n".join(lines) or "(no attack data)"


def gate_env(
    env_dir: str,
    model: str = DEFAULT_ATTACKER,
    solve_threshold: float = DEFAULT_SOLVE_THRESHOLD,
    discard: bool = True,
) -> GateResult:
    """Attempt one environment once; discard it if solved first try."""
    env_id = os.path.basename(os.path.normpath(env_dir))
    log_dir = os.path.join(env_dir, "gate_logs")
    try:
        score = _eval_env(env_dir, model, log_dir)
    except Exception as e:
        return GateResult(
            env_id=env_id,
            model=model,
            score=None,
            solved=False,
            discarded=False,
            error=f"{type(e).__name__}: {e}",
        )
    solved = score >= solve_threshold
    result = GateResult(env_id=env_id, model=model, score=score, solved=solved, discarded=False)
    with open(os.path.join(env_dir, "gate_result.json"), "w") as f:
        json.dump(asdict(result) | {"solve_threshold": solve_threshold}, f, indent=1)
    if solved and discard:
        discard_root = os.path.join(
            os.path.dirname(os.path.normpath(env_dir)), "discarded_too_easy"
        )
        os.makedirs(discard_root, exist_ok=True)
        shutil.move(env_dir, os.path.join(discard_root, env_id))
        result.discarded = True
    return result


def gate_all(
    envs_root: str,
    model: str = DEFAULT_ATTACKER,
    solve_threshold: float = DEFAULT_SOLVE_THRESHOLD,
    discard: bool = True,
) -> list[GateResult]:
    """Gate every exported environment under a root; writes a summary report."""
    results: list[GateResult] = []
    for name in sorted(os.listdir(envs_root)):
        env_dir = os.path.join(envs_root, name)
        if not os.path.isfile(os.path.join(env_dir, "task.py")):
            continue
        results.append(
            gate_env(env_dir, model=model, solve_threshold=solve_threshold, discard=discard)
        )
    with open(os.path.join(envs_root, "gate_report.json"), "w") as f:
        json.dump([asdict(r) for r in results], f, indent=1)
    return results
