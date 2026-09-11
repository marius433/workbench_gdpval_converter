"""Pairwise judging: which of two anonymised submissions is the better work.

Per task, every pair of players (workers + gold) is judged in BOTH orders so
position bias cancels; the judge never learns which side is the expert.
Judgments append to ``<export>/judgments.jsonl`` and already-judged pairs are
skipped, so the stage is resumable and new workers only add new pairs.
"""

from __future__ import annotations

import json
import os
from itertools import combinations

from ..rlgen.llm import LLMClient, extract_json
from .extract import submission_text
from .runner import _model_dirname, load_rows

GOLD = "gold"

_SYSTEM = """You are an expert reviewer ranking two candidate deliverable sets for the same
professional assignment. You see the assignment, the grading guidance, and the two
submissions (A and B) as extracted text.

Judge the work itself: correctness of the analysis, use of the source material,
fulfilment of the brief, and craft. Formatting artefacts of text extraction are not
evidence either way. Do not reward length, hedging, or volume of verified-sounding
figures; do reward correct, committed, decision-ready work.

Return ONLY a JSON object: {"winner": "A" | "B" | "tie", "reasoning": "<2-4 sentences>"}"""


def _pair_key(task_id: str, a: str, b: str, swapped: bool) -> str:
    return f"{task_id}|{a}|{b}|{int(swapped)}"


def _select_pairs(players: list[str], workers: list[str], strategy: str) -> list[tuple[str, str]]:
    """Pairs to judge for one task, per strategy (see judge_export)."""
    if strategy == "full":
        return list(combinations(players, 2))
    if strategy != "sparse":
        raise ValueError(f"unknown pair strategy {strategy!r}")
    pairs: list[tuple[str, str]] = []
    if GOLD in players:
        pairs.extend((w, GOLD) for w in workers)
    if len(workers) == 2:
        pairs.append((workers[0], workers[1]))
    elif len(workers) > 2:
        pairs.extend((workers[i], workers[(i + 1) % len(workers)]) for i in range(len(workers)))
    return pairs


def _judge_user(prompt: str, rubric: str, text_a: str, text_b: str) -> str:
    return (
        f"THE ASSIGNMENT:\n{prompt}\n\n"
        f"GRADING GUIDANCE:\n{rubric}\n\n"
        f"SUBMISSION A:\n{text_a}\n\n"
        f"SUBMISSION B:\n{text_b}\n\n"
        'Which submission is the better piece of work? JSON only: {"winner": ..., "reasoning": ...}'
    )


def judge_export(
    export_dir: str,
    client: LLMClient,
    workers: list[str],
    task_ids: list[str] | None = None,
    include_gold: bool = True,
    max_prompt_chars: int = 8_000,
    max_rubric_chars: int = 12_000,
    pairs: str = "full",
) -> dict[str, int]:
    """Judge pairs for the selected tasks. Returns a summary of counts.

    ``pairs``:
    - ``full`` — every player pair (quadratic in players).
    - ``sparse`` — every worker vs gold (the hardness anchor) plus a ring
      over the workers, which keeps the Bradley-Terry comparison graph
      connected at ~2 pairs per worker instead of all C(n,2).

    A player with no submission for a task (failed or absent run) is left
    out of that task's pairs rather than judged as an empty document.
    """
    rows = load_rows(export_dir, task_ids)
    out_path = os.path.join(export_dir, "judgments.jsonl")
    done: set[str] = set()
    if os.path.isfile(out_path):
        with open(out_path) as f:
            for line in f:
                j = json.loads(line)
                done.add(_pair_key(j["task_id"], j["player_a"], j["player_b"], j["swapped"]))

    new, skipped, errors = 0, 0, 0
    with open(out_path, "a") as out:
        for row in rows:
            task_id = str(row["task_id"])
            dirs = {
                w: os.path.join(export_dir, "runs", _model_dirname(w), task_id) for w in workers
            }
            if include_gold:
                dirs[GOLD] = os.path.join(export_dir, task_id, "gold")
            texts = {p: submission_text(d) for p, d in dirs.items()}
            present_workers = [w for w in workers if texts[w] != "(no submission)"]
            players = present_workers + ([GOLD] if include_gold else [])
            for a, b in _select_pairs(players, present_workers, pairs):
                for swapped in (False, True):
                    if _pair_key(task_id, a, b, swapped) in done:
                        skipped += 1
                        continue
                    first, second = (b, a) if swapped else (a, b)
                    user = _judge_user(
                        str(row["prompt"])[:max_prompt_chars],
                        str(row["rubric_pretty"])[:max_rubric_chars],
                        texts[first],
                        texts[second],
                    )
                    record = {
                        "task_id": task_id,
                        "player_a": a,
                        "player_b": b,
                        "swapped": swapped,
                        "judge_model": client.config.model,
                    }
                    try:
                        verdict = extract_json(client.complete(_SYSTEM, user))
                        raw = str(verdict.get("winner", "")).strip().upper()
                        if raw == "TIE":
                            record["winner"] = "tie"
                        elif raw in ("A", "B"):
                            # 'A' is whoever was shown first; map back to the player.
                            record["winner"] = first if raw == "A" else second
                        else:
                            record["winner"] = "invalid"
                        record["reasoning"] = str(verdict.get("reasoning", ""))[:1000]
                    except Exception as e:
                        record["winner"] = "error"
                        record["reasoning"] = f"{type(e).__name__}: {e}"
                        errors += 1
                    out.write(json.dumps(record) + "\n")
                    out.flush()
                    new += 1
    return {"new": new, "skipped": skipped, "errors": errors, "path": out_path}  # type: ignore[dict-item]
