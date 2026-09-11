"""Private GDPval-AA-style Elo grading for a converted export.

Replicates the AA v2 methodology on our own tasks, anchored on our own
goldens (the resulting ratings are NOT comparable to published GDPval-AA v2
Elo — the 220-task set is closed; say so whenever numbers leave the team):

1. **run** — workers attempt every task in a Docker sandbox; deliverables are
   collected under ``runs/<model>/<task_id>/`` (``runner.py``).
2. **judge** — an LLM judge compares anonymised deliverable pairs per task
   (every worker vs every other and vs ``gold/``), guided by the task's
   rubric, in both orders to cancel position bias (``judge.py``).
3. **elo** — a Bradley-Terry fit over the pairwise results, anchored at
   gold = 1000, plus a per-task hardness report (``elo.py``).

Hardness readout: a task is *hard enough* when every worker systematically
loses to gold; *too easy* when a worker beats or ties gold; *degenerate* when
nothing separates the workers (all lose everything).
"""
