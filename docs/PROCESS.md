# WorkBench → GDPval conversion — process manual

Standing process for converting any WorkBench bundle (current or future MEs)
into GDPval-format packages, and for generating RL environments from the same
material. The repeatable asset is the `wb2gdpval` package in this repo; this
document is its manual. Improve the code, don't re-derive its logic. If
conversion rules and this manual ever disagree, the manual is wrong — fix the
manual.

## Invocation

```bash
uv run wb2gdpval convert <bundle_dir> -o <exports_root>                # Mode A (one2one)
uv run wb2gdpval convert <bundle_dir> -o <exports_root> --mode split   # Mode B
uv run wb2gdpval check <export_dir> --bundle <bundle_dir>              # re-run acceptance
```

Output: `<exports_root>/<bundle-name>_<YYYY-MM-DD>[_split]/` — never
overwritten; a second run the same day gets a `_runN` suffix. Each export
contains one dir per task (`task.json`, `references/`, `gold/`, `gold_alt/`,
`rubric_source.md`), a `conversion_report.csv`, `gdpval_tasks.jsonl`,
`SHIP_NOTES.md`, `stirrup_runner.py`, and a `manifest.json` recording
converter version, mode, source path, counts and a flag summary. The manifest
is the audit trail: keep it with anything shipped.

Per-bundle settings (category join, occupation map, task glob, expected
counts, anchors) live in `configs/bundles.yaml`, keyed by the bundle
directory basename. CLI flags override config values.

## What the conversion is

GDPval format = fully-specified prompt + reference files + expert gold,
one-shot, graded externally by pairwise comparison. WorkBench hides the brief
in the environment; the conversion **relocates** the brief text into the
prompt. It never rewrites, summarises or improves the expert's words — that
protects the "nothing synthetic" claim. Concatenate and clean only.

Rules implemented by the converter (authoritative order):

1. Prompt = task description + cleaned text of each brief file (header block
   stripped — note the line immediately after the headers, in practice the
   salutation, is consumed with them; see `textio.clean_email`). Pointers at
   consumed files are rewritten to "the brief below".
2. Brief detection: files named in the description → `01_brief/` contents →
   name-pattern match (email/request/brief/kick/note_from/memo_from),
   punctuation-insensitive, `.docx/.txt/.md/.msg`. None found is flagged, not
   fatal — some tasks carry the brief in the description.
3. Deliverables: parsed from the prompt; fallback to grading.yaml
   `file_exists` targets (flagged). `report.json` is never a deliverable.
4. References ship minus consumed briefs and macOS junk. `.msg` files are
   re-emitted as `.txt` (they are MIME/RFC-822 despite the extension; a .msg
   is not openable by sandbox tooling). Chained tasks need nothing: upstream
   gold already sits in `02_handoffs/`.
5. Occupation/sector from the category table; extend via `occupation_map.json`
   in the bundle root, a per-bundle `occupation_map` config entry, or
   `--occupation-map`. Keys may be categories or bare task ids (per-task
   override wins). Unmapped categories are recorded in the manifest — never
   guess them. Sector always follows the occupation via the GDPval pairing.
6. `grading.yaml`, `llm_output/`, `reports/` never ship *to the model* — but
   the rubric (grading_prompt.md + grading.yaml's `llm_judge.rubric`) ships
   as `rubric_pretty` / `rubric_json` dataset metadata, de-coupled from the
   WorkBench harness (whitelisted plumbing sentences removed, GDPval grading
   preamble prepended, untouched original kept as `rubric_source.md`).
   Residual coupling is flagged with snippets, never regex-edited —
   "transcript" is ambiguous (P06/P07 grade *interview* transcripts).
7. Gold hygiene: `report.json` and duplicate renderings are quarantined in
   `gold_alt/` so the expert submission has the same shape as a model's.
8. A prompt that cites a folder conversion emptied or the source renamed is
   flagged, never rewritten.

## Mode B — split by deliverable

A task whose prompt asks for N > 1 deliverables becomes N child tasks
(`<task>-D01`, `-D02`, …), each carrying: the parent's full prompt plus a
clearly-labelled mechanical scope note ("produce only `X`"), the parent's
references unchanged, the gold file(s) for that deliverable only (same-stem
alternates travel to the child's `gold_alt/`), and the whole rubric behind a
split-scope preamble. `report.json` in a prompt's deliverable list never
becomes a child. Single-deliverable tasks pass through unchanged.

Interdependence cannot be sliced mechanically. Children whose rubric names a
sibling deliverable are flagged `deliverables may be interdependent` — but
the detector only sees filenames/stems, so prose references ("the cover
note") escape it. **Every split export needs a human interdependence pass**
before shipping; a cover note that explains a deck may not stand alone as a
task, whatever the flags say.

## Mode C2 — RL environments with programmatic rewards

See README.md for the pipeline (`rlgen generate → validate → export → gate`).
Process rules:

- A candidate's every numeric check must carry a derivation over
  file-grounded refs; the validator recomputes it from the actual files and
  rejects the whole candidate on any unverifiable fact. No asserted fact is
  trusted, ever.
- The difficulty gate (strong model attempts the exported inspect_ai task;
  solved-first-try is discarded to `discarded_too_easy/`) is mandatory before
  any env ships. Keep the gate report with the shipped set.
- Generated envs are synthetic by construction — they carry no "nothing
  synthetic" claim. State that when shipping.

## Per-bundle blocks

Recorded counts and anchors now live in `configs/bundles.yaml` (the machine
half). The narrative history below is the human half — keep both when
recording a new bundle. If no anchors exist yet, run once, inspect, then
write them — the second run is the accountable one.

### PetroNusa_Project_Wayang_50_evals (ME-A-01, pack layout)

- Expected under 1.x flag semantics: 50 tasks; 36 clean / 14 flagged
  (5 no-brief, 9 grading.yaml fallback, 1 unparsed, 0 golden missing).
- Anchor task: ME-A-01-P20 — prompt contains "Marcus wants the Phase 1 site
  visit plan"; no From:/To: residue; deliverables ==
  [PetroNusa_Phase1_CoveringNote.docx, PetroNusa_Phase1_SiteVisitPlan.xlsx];
  references exclude Nadia_SiteVisitPlan_Request.docx; gold/ non-empty.
- Categories all mapped (strategy / operational_dd / market_sizing).

### 20260902_workbench_ME_nikita_final (ME-A-01, review-format revision)

- Layout: flat review-format (`user_prompt/`, `grader/`, `reference/`,
  `golden/`, `working-files/`), task dirs suffixed `_vF`. No `task.yaml`, so
  `category` is joined from `20260824_ME_Nikita` (configured), which supplies
  all 50 (32 strategy / 15 operational_dd / 3 market_sizing).
- **Final accountable run (2026-09-02, converter 1.4.0, `_run5`)**: 50 tasks,
  11 clean / 39 flagged. Per-task queue: 9 grading.yaml fallback, dangling
  folder pointers on P07 (x2, stale in source), P24 (consumed) and P31.
  Systemic: 34 tasks carry 101 residual rubric-coupling mentions, flagged
  with snippets, not edited. Briefs inline on 50/50; every prompt clears
  GDPval's 617-char floor. 6 occupations / 5 sectors, applied per task.
- **Converter 2.0.0 (2026-09-10) reproduces `_run5` byte-for-byte**
  (conversion_report.csv, gdpval_tasks.jsonl, all 50 task.json, file trees).
  Counts recorded in `configs/bundles.yaml`.
- **Mode B recorded run (2026-09-10, converter 2.0.0)**: 85 tasks from 50
  (34 parents → 69 children, 16 pass-throughs — including P17, whose prompt
  lists report.json as a deliverable; excluded from splitting). 30 children
  flagged possibly interdependent; 0 missing gold slices. Human
  interdependence pass **not yet done** — required before shipping.
- Known gap for the human pass: several `.msg` files in `01_brief/` across
  P02/P03/P04/P31 are skipped silently because a `.docx` brief sits alongside.

### <next bundle>

- Run once (observing), inspect, record counts + anchor in
  `configs/bundles.yaml`, and add a narrative block here.
- New categories: add to the bundle's `occupation_map.json`, note here.

## Acceptance checks (every run, any bundle)

Run automatically after `wb2gdpval convert` (non-zero exit on failure):

- Manifest counts match the recorded block; on a first run, observed counts
  are printed to record instead.
- Anchor tasks pass their listed assertions.
- No macOS junk files in the export.
- Every task dir has task.json + references/ + gold/.
- Flagged tasks listed and handed to a human — flags are review queues, not
  errors.

## Human passes before any external share

1. Read ~10 prompts for readability as one-shot instructions.
2. Review occupation tags per task (table mapping is deliberately crude).
3. Split exports: interdependence pass over every flagged child, and over
   unflagged children whose rubric references siblings in prose.
4. Decision on record: these are derivatives of the same goldens and files —
   shipping them spends the source tasks as held-out material for that client.

## Target format: GDPval-AA v2 (Artificial Analysis)

The client runs the `openai/gdpval` 220-task set through the Stirrup harness
and rates models by Elo. Facts that constrain this converter, verified
against the dataset and the harness source:

- **Schema** (`openai/gdpval`, 220 rows): `task_id`, `sector`, `occupation`,
  `prompt`, `reference_files`, `deliverable_files`, `rubric_pretty`,
  `rubric_json`. Emitted as `gdpval_tasks.jsonl`; our conversion provenance
  stays in the per-task `task.json` sidecar and never enters the dataset row.
- **Occupation determines sector.** No occupation in GDPval spans two
  sectors — `GDPVAL_SECTOR` in `occupations.py` is the authoritative pairing,
  derived from all 220 rows. Widening sector coverage is therefore only
  possible by choosing a richer occupation set.
- **Gold enters the comparison as a submission** — the Elo scale is anchored
  to human expert deliverables at 1000. So gold must look like a model
  submission: no `report.json`, exactly one rendering per artifact.
- **Stirrup file staging**: `upload_files` preserves structure for a
  DIRECTORY but flattens a file list to basenames. The runner must pass
  `references/` as a directory or every prompt that names a subfolder breaks.
- **Bands to watch** (ours vs theirs): prompt 617–6,620 chars;
  `reference_files` 0–17; `deliverable_files` 0–6.
- **Not joinable.** GDPval-AA v2 is a closed 220-task set. A converted bundle
  can reproduce the methodology privately, anchored on its own goldens, but
  its Elo is not comparable to published numbers — say so in writing before
  shipping (SHIP_NOTES.md states it per export).

## Changing the code

Version lives in `src/wb2gdpval/__init__.py`; bump it on any behavioural
change and note the change below. Mode A output is baselined byte-for-byte
against recorded runs — a behavioural change means re-baselining every bundle
in `configs/bundles.yaml` and updating its narrative block here. Run
`uv run pytest && uv run ruff check && uv run mypy` before committing.

## Changelog

- 2.0.0 — repackaged as the `wb2gdpval` uv project (this repo): modules,
  typed, tested (synthetic fixtures + real-bundle integration), CLI with
  subcommands, config-driven bundles (`configs/bundles.yaml`) with
  observe-then-assert acceptance checks and anchors. Mode A verified
  byte-identical to the 1.4.0 `_run5` accountable export. New **Mode B**
  (`--mode split`): split-by-deliverable with gold slicing, labelled scope
  notes, sibling-mention interdependence flags; report.json never becomes a
  child. New **Mode C2** (`rlgen`): model-generated RL environments with
  derivation-backed programmatic rewards, deterministic fact validation
  against the actual files, export as self-contained inspect_ai Docker
  tasks, and a pass@1 difficulty gate that discards envs a strong model
  solves first try.
- 1.4.0 — `.msg` briefs inlined and re-emitted as `.txt`; rubric de-coupling
  whitelist + GDPval preamble, residuals flagged never regex-edited;
  generalised dangling-folder detection; per-task occupation overrides;
  SHIP_NOTES.md. (Final single-file version — see the bundle's
  GDPVAL_CONVERSION_PROCESS.md for the full 1.x changelog.)
- 1.3.0 — GDPval-AA v2 target: gdpval_tasks.jsonl, stirrup_runner.py,
  rubrics as dataset metadata, gold hygiene, sector-from-occupation.
- 1.2.0 — review-format layout adapter, `--category-from` join, task-id
  normalisation.
- 1.1.0 — versioned export dirs, manifest.json, external occupation_map.json.
- 1.0.0 — initial: prompt merge, brief detection, deliverable parsing, flags.
