"""Bradley-Terry fit over pairwise judgments, anchored at gold = 1000.

Ties count half a win to each side. The fit is the standard
minorization-maximization iteration on BT strengths; ratings convert to the
Elo scale as ``1000 + (400/ln 10) * ln(strength / strength_gold)``, so gold
sits at exactly 1000 like the human-expert anchor in GDPval-AA v2. The
numbers are NOT comparable to published AA v2 Elo — closed task set, other
judges — and every report this module writes says so.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from typing import Any

from .judge import GOLD

Judgment = dict[str, Any]


def load_judgments(export_dir: str) -> list[Judgment]:
    path = os.path.join(export_dir, "judgments.jsonl")
    with open(path) as f:
        rows = [json.loads(line) for line in f]
    return [r for r in rows if r.get("winner") not in ("error", "invalid")]


def _win_matrix(judgments: list[Judgment]) -> dict[str, dict[str, float]]:
    """wins[a][b] = (possibly fractional) wins of a over b."""
    wins: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for j in judgments:
        a, b, w = j["player_a"], j["player_b"], j["winner"]
        if w == "tie":
            wins[a][b] += 0.5
            wins[b][a] += 0.5
        else:
            loser = b if w == a else a
            wins[w][loser] += 1.0
    return wins


def bradley_terry_elo(
    judgments: list[Judgment], anchor: str = GOLD, iterations: int = 500
) -> dict[str, float]:
    """Fit BT strengths (MM algorithm) and return Elo ratings, anchor = 1000."""
    wins = _win_matrix(judgments)
    players = sorted({p for j in judgments for p in (j["player_a"], j["player_b"])})
    if anchor not in players:
        raise ValueError(f"anchor {anchor!r} has no judgments")
    games: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for a in players:
        for b in players:
            if a < b:
                n = wins[a][b] + wins[b][a]
                games[a][b] = games[b][a] = n
    strength = {p: 1.0 for p in players}
    for _ in range(iterations):
        new = {}
        for p in players:
            total_wins = sum(wins[p].values())
            denom = sum(
                games[p][q] / (strength[p] + strength[q])
                for q in players
                if q != p and games[p][q] > 0
            )
            # Regularised so an undefeated (or winless) player stays finite.
            new[p] = (total_wins + 0.5) / (denom + 1.0 / (strength[p] + 1.0))
        norm = sum(new.values()) / len(new)
        strength = {p: v / norm for p, v in new.items()}
    scale = 400.0 / math.log(10.0)
    return {p: 1000.0 + scale * math.log(strength[p] / strength[anchor]) for p in players}


def per_task_vs_gold(judgments: list[Judgment]) -> dict[str, dict[str, dict[str, float]]]:
    """score[task][worker] = {points, games, rate} against gold (ties = 0.5)."""
    table: dict[str, dict[str, dict[str, float]]] = defaultdict(dict)
    for j in judgments:
        pair = {j["player_a"], j["player_b"]}
        if GOLD not in pair:
            continue
        worker = next(iter(pair - {GOLD}), GOLD)
        rec = table[j["task_id"]].setdefault(worker, {"points": 0.0, "games": 0.0})
        rec["games"] += 1
        if j["winner"] == "tie":
            rec["points"] += 0.5
        elif j["winner"] == worker:
            rec["points"] += 1.0
    for task in table.values():
        for rec in task.values():
            rec["rate"] = rec["points"] / rec["games"] if rec["games"] else 0.0
    return table


def _verdict(rates: list[float]) -> str:
    if not rates:
        return "no data"
    if max(rates) >= 0.5:
        return "TOO EASY — a worker beats or ties gold"
    if max(rates) <= 0.25:
        return "hard"
    return "adequate"


def hardness_report(export_dir: str) -> str:
    """Write grade_report.md + grade_report.json; returns the markdown path."""
    judgments = load_judgments(export_dir)
    ratings = bradley_terry_elo(judgments)
    per_task = per_task_vs_gold(judgments)

    lines = [
        "# Private Elo grading report",
        "",
        "Bradley-Terry over pairwise LLM judgments, anchored at gold = 1000.",
        "**Not comparable to published GDPval-AA v2 Elo** (closed 220-task set,",
        "different judges); this rating stands alone on this export's goldens.",
        "",
        f"Judgments used: {len(judgments)}",
        "",
        "## Ratings",
        "",
        "| Player | Elo |",
        "| --- | --- |",
    ]
    for p, r in sorted(ratings.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {p} | {r:.0f} |")
    lines += ["", "## Per-task hardness (worker win-rate vs gold, ties = 0.5)", ""]
    workers = sorted({w for t in per_task.values() for w in t})
    lines.append("| Task | " + " | ".join(workers) + " | Verdict |")
    lines.append("| --- |" + " --- |" * (len(workers) + 1))
    verdicts: dict[str, str] = {}
    for task_id in sorted(per_task):
        rates = [per_task[task_id].get(w, {}).get("rate", 0.0) for w in workers]
        verdicts[task_id] = _verdict(rates)
        lines.append(
            f"| {task_id} | " + " | ".join(f"{r:.2f}" for r in rates) + f" | {verdicts[task_id]} |"
        )
    md_path = os.path.join(export_dir, "grade_report.md")
    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(export_dir, "grade_report.json"), "w") as f:
        json.dump(
            {"ratings": ratings, "per_task_vs_gold": per_task, "verdicts": verdicts},
            f,
            indent=1,
        )
    return md_path
