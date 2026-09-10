"""Mode C2: model-generated RL environments with programmatic rewards.

Pipeline (each stage is a CLI subcommand and a function):

1. **generate** — a model reads a converted ME task (prompt + reference file
   contents) and proposes new, hard assignments, each with machine-checkable
   reward checks grounded in the ME's facts (``generate.py``).
2. **validate** — a deterministic validator confirms every asserted fact is
   actually in the environment: cell references resolve, quotes appear,
   derivations recompute to the expected value (``validate.py``). No LLM.
3. **export** — validated candidates become self-contained inspect_ai tasks
   with a Docker sandbox and a programmatic scorer (``export_inspect.py``).
4. **gate** — a strong model attempts each exported task; anything solved on
   the first attempt is discarded as too easy (``difficulty.py``). This gate
   is the difference between generated tasks and generated slop.

No golden deliverable is needed anywhere: the reward is computable.
"""
