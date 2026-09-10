# workbench_gdpval_converter

Converts WorkBench task bundles (macro environments / MEs) into GDPval-format
task packages, and generates RL environments with programmatic rewards from
the same material.

Three modes:

| Mode | Command | What it does | Model needed |
| --- | --- | --- | --- |
| **A — one2one** | `wb2gdpval convert <bundle>` | 1:1 deterministic conversion: one WorkBench task → one GDPval task (prompt + references + expert gold + rubric metadata) | no |
| **B — split** | `wb2gdpval convert <bundle> --mode split` | Split-by-deliverable: a task with N deliverables becomes N tasks, gold and scope sliced per target file | no |
| **C2 — rlgen** | `wb2gdpval rlgen …` | Model-authored RL environments: new assignments over the same data room, rewarded by machine-verified checks (no golden needed) | yes |

The deterministic modes never rewrite expert prose — briefs are *relocated*
into the prompt, harness plumbing is removed by whitelist, and every
ambiguity is **flagged, not guessed**. Flags are review queues, not errors.

`docs/PROCESS.md` is the standing process manual (per-bundle blocks, recorded
accountable runs, GDPval-AA v2 target facts, changelog). Read it before
converting a new bundle or changing conversion rules.

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # converter (modes A and B) + dev tools
uv sync --extra llm      # + OpenAI-compatible client for Mode C2 generation
uv sync --extra inspect  # + inspect-ai for the C2 difficulty gate
```

## Converting a bundle (modes A and B)

```bash
uv run wb2gdpval convert /path/to/bundle -o ./exports                # Mode A
uv run wb2gdpval convert /path/to/bundle -o ./exports --mode split   # Mode B
```

Output: `exports/<bundle>_<date>[_split][ _runN]/` — never overwritten. Each
export contains one directory per task (`task.json` provenance sidecar,
`references/`, `gold/`, `gold_alt/`, `rubric_source.md`), plus:

- `gdpval_tasks.jsonl` — the dataset in the openai/gdpval schema
- `conversion_report.csv` — per-task flag queue for the human pass
- `manifest.json` — the audit trail; keep it with anything shipped
- `SHIP_NOTES.md` — comparability limits and distribution divergences
- `stirrup_runner.py` — runs the export through the Stirrup agent harness

### Per-bundle configuration and acceptance

Bundles are configured in `configs/bundles.yaml`, keyed by the bundle
directory basename — a new bundle needs a config entry, not code changes.
The entry can carry `category_from` (pack-layout bundle to join `category`
from when the review format dropped it), `occupation_map`, `task_glob`,
recorded `expected` counts per mode, and `anchors` (hand-picked tasks with
distinctive assertions).

The acceptance workflow is **first run observes, second run asserts**:

1. Run an unconfigured bundle; the CLI reports observed counts.
2. Inspect the export, record the counts and an anchor in `configs/bundles.yaml`.
3. Every subsequent run is checked against them — mismatches exit non-zero.

`wb2gdpval check <export_dir> --bundle <bundle_dir>` re-runs the checks on an
existing export.

## Mode C2: RL environments with programmatic rewards

A GDPval task needs an expert golden to grade against; an RL environment does
not — the reward is computable. The pipeline is **generate → validate →
export → gate**, and difficulty is enforced twice: the validator rejects
lookup-only rewards, and the gate discards anything a strong model solves in
one pass.

```bash
# 1. A model reads one ME task (prompt + full reference contents, spreadsheets
#    dumped with cell addresses) and proposes hard new assignments, each with
#    derivation-backed reward checks.
uv run wb2gdpval rlgen generate /path/to/bundle/ME-A-01-P01_vF -o ./rl_out -n 5

# 2. Deterministic validation (no LLM): every cell ref is read, every quote
#    searched, every derivation recomputed. Unverifiable facts reject the
#    candidate — asserted facts are never trusted.
uv run wb2gdpval rlgen validate ./rl_out/ME-A-01-P01

# 3. Validated candidates become self-contained inspect_ai tasks
#    (Docker sandbox + programmatic scorer over the agent's report.json).
uv run wb2gdpval rlgen export ./rl_out/ME-A-01-P01 -o ./rl_out/envs

# 4. Difficulty gate: a strong model attempts each env once; anything scoring
#    >= threshold is moved to discarded_too_easy/ (report in gate_report.json).
uv run wb2gdpval rlgen gate ./rl_out/envs --model anthropic/claude-opus-5
```

Each exported env runs standalone: `inspect eval task.py --model <model>`
(Docker required). The scorer is pure code — it reads `report.json` and the
deliverable files from the sandbox and applies `checks.json`.

### LLM configuration

Generation talks to any OpenAI-compatible endpoint (litellm proxy via
keygate, OpenRouter, OpenAI). Flags `--model` / `--base-url` win; otherwise:

| Variable | Meaning |
| --- | --- |
| `WB2GDPVAL_LLM_BASE_URL` (or `OPENAI_BASE_URL`) | endpoint base URL |
| `WB2GDPVAL_LLM_API_KEY` (or `LITELLM_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`) | API key — first one set wins |
| `WB2GDPVAL_LLM_MODEL` | default model id (default `claude-opus-5`; on OpenRouter use `anthropic/claude-opus-5`) |

The difficulty gate goes through inspect-ai, which reads its own provider
variables (`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, …).

## Development

```bash
uv run pytest          # includes a real-bundle integration test, skipped when absent
uv run ruff format && uv run ruff check --fix
uv run mypy            # strict
```

Test fixtures are synthetic and generated at test time (`tests/conftest.py`)
— no client-derived material is committed. The integration test
(`tests/test_integration_real_bundle.py`) asserts the recorded accountable
counts against the real ME-A-01 bundle when it exists locally.

Layout:

```text
src/wb2gdpval/
  cli.py           command line (convert / check / rlgen …)
  convert.py       Mode A core: one task dir -> TaskConversion
  split.py         Mode B: split-by-deliverable slicing
  export.py        export writer: task dirs, jsonl, manifest, ship notes, runner
  acceptance.py    observe/assert acceptance checks
  config.py        configs/bundles.yaml loading
  layouts.py       pack vs review layout detection
  briefs.py        brief detection (relocated into the prompt)
  prompts.py       prompt assembly, deliverable parsing
  rubric.py        rubric extraction + harness de-coupling
  gold.py          gold hygiene (report.json / duplicate renderings quarantined)
  occupations.py   occupation/sector resolution (GDPval pairing)
  textio.py        docx/.msg/email text extraction
  rlgen/           Mode C2: generate / validate / export_inspect / difficulty
```

### Invariants — do not break

- **Mode A reproduces recorded runs byte-for-byte.** The ME-A-01 accountable
  run (50 tasks, 11 clean / 39 flagged; process manual, converter 1.4.0) is
  the baseline; 2.0.0 reproduces its CSV, JSONL and every `task.json`
  exactly. Any behavioural change must bump the version, update
  `docs/PROCESS.md`, and re-baseline every configured bundle.
- **Never rewrite expert prose.** Relocate, remove whole whitelisted plumbing
  sentences, append clearly-labelled conversion notes — nothing else.
- **Never guess.** Unmapped occupations stay `UNMAPPED`; unparseable
  deliverables, dangling folder pointers and residual rubric coupling are
  flagged for the human queue.
- **Golds must look like submissions.** No `report.json`, one rendering per
  artifact; everything dropped is quarantined in `gold_alt/`, never deleted.
- **C2 checks are verified, never trusted.** A candidate whose facts the
  validator cannot recompute from the actual files is rejected whole.
