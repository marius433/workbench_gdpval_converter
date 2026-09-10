"""Bundle export: run a conversion mode over a bundle and write the export.

Output layout (per export, never overwritten — a second run the same day gets
a ``_runN`` suffix):

- one directory per task: ``task.json`` (provenance sidecar), ``references/``,
  ``gold/`` (the graded expert deliverable), ``gold_alt/`` (quarantined),
  ``rubric_source.md`` (un-decoupled rubric, for audit)
- ``gdpval_tasks.jsonl`` — the dataset in the openai/gdpval schema
- ``conversion_report.csv`` — per-task flag queue
- ``manifest.json`` — the audit trail; keep it with anything shipped
- ``SHIP_NOTES.md`` — comparability limits and distribution divergences
- ``stirrup_runner.py`` — runs the export through the Stirrup agent harness
"""

from __future__ import annotations

import csv
import datetime
import glob
import json
import os
import shutil
from dataclasses import dataclass, field

from . import __version__
from .convert import convert_task
from .models import PROMPT_CHARS_MAX, PROMPT_CHARS_MIN, TaskConversion
from .occupations import UNMAPPED, OccupationResolver
from .split import split_task

MODE_ONE2ONE = "one2one"
MODE_SPLIT = "split"
MODES = (MODE_ONE2ONE, MODE_SPLIT)

_FLAG_SUMMARY_KEYS = (
    "no brief file found",
    "deliverables taken from grading.yaml",
    "deliverable filenames not parsed",
    "golden missing",
    "brief unreadable",
    "category unavailable",
    "unrecognised task layout",
    "pointer dangling",
    "rubric residual coupling",
    "no gradeable gold artifact",
    ".msg unreadable",
)
_SPLIT_FLAG_SUMMARY_KEYS = (
    "split from",
    "gold missing for split deliverable",
    "deliverables may be interdependent",
)


@dataclass
class ExportResult:
    out_dir: str
    manifest: dict[str, object]
    report_rows: list[dict[str, object]] = field(default_factory=list)


def _make_out_dir(bundle_name: str, exports_root: str, mode: str) -> str:
    stamp = datetime.date.today().isoformat()
    base = f"{bundle_name}_{stamp}" if mode == MODE_ONE2ONE else f"{bundle_name}_{stamp}_{mode}"
    out = os.path.join(exports_root, base)
    n = 2
    while os.path.exists(out):
        out = os.path.join(exports_root, f"{base}_run{n}")
        n += 1
    os.makedirs(out)
    return out


def _write_task(out_root: str, conv: TaskConversion, source_bundle_name: str) -> None:
    tdir = os.path.join(out_root, conv.task_id)
    refs_out = os.path.join(tdir, "references")
    if os.path.exists(tdir):
        shutil.rmtree(tdir)
    os.makedirs(refs_out)
    for r in conv.refs:
        dst = os.path.join(refs_out, r.rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if r.msg_txt is not None:
            with open(dst, "w") as fh:
                fh.write(r.msg_txt)
        else:
            shutil.copy2(r.src, dst)
    if conv.golden_present:
        gdst = os.path.join(tdir, "gold")
        os.makedirs(gdst, exist_ok=True)
        for g in sorted(conv.gold, key=lambda g: g.name):
            shutil.copy2(g.src, os.path.join(gdst, g.name))
        if conv.gold_alt:
            adst = os.path.join(tdir, "gold_alt")
            os.makedirs(adst, exist_ok=True)
            for g in sorted(conv.gold_alt, key=lambda g: g.name):
                shutil.copy2(g.src, os.path.join(adst, g.name))
    with open(os.path.join(tdir, "task.json"), "w") as f:
        json.dump(conv.to_task_json(source_bundle_name), f, indent=1)
    with open(os.path.join(tdir, "rubric_source.md"), "w") as fh:
        fh.write(conv.rubric.pretty_source)


def run_export(
    bundle_dir: str,
    exports_root: str,
    mode: str = MODE_ONE2ONE,
    category_from: str | None = None,
    occupation_map: str | None = None,
    task_glob: str = "ME-*",
    source_name: str | None = None,
) -> ExportResult:
    """Convert every task in a bundle and write a versioned export.

    ``source_name`` labels the provenance rows (defaults to the bundle dir
    basename); ``task_glob`` selects task directories inside the bundle.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    bundle_name = os.path.basename(os.path.abspath(bundle_dir)) or "bundle"
    out = _make_out_dir(bundle_name, exports_root, mode)

    resolver = OccupationResolver()
    maps_loaded = resolver.load_maps_near(bundle_dir, occupation_map)

    report: list[dict[str, object]] = []
    conversions: list[TaskConversion] = []
    layouts: dict[str, int] = {}
    n_source_tasks = 0
    for d in sorted(glob.glob(os.path.join(bundle_dir, task_glob))):
        if not os.path.isdir(d):
            continue
        n_source_tasks += 1
        conv = convert_task(d, resolver, category_from=category_from)
        if conv is None:
            from .layouts import normalise_tid

            report.append(
                dict(
                    task=normalise_tid(os.path.basename(d)),
                    briefs=0,
                    refs=0,
                    deliv=0,
                    flags="unrecognised task layout; not converted",
                )
            )
            continue
        layouts[conv.layout] = layouts.get(conv.layout, 0) + 1
        emitted = split_task(conv) if mode == MODE_SPLIT else [conv]
        for child in emitted:
            _write_task(out, child, source_name or bundle_name)
            conversions.append(child)
            report.append(
                dict(
                    task=child.task_id,
                    briefs=len(child.briefs_merged),
                    refs=len(child.refs),
                    deliv=len(child.deliverables),
                    flags="; ".join(child.flags),
                )
            )

    with open(os.path.join(out, "conversion_report.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task", "briefs", "refs", "deliv", "flags"])
        w.writeheader()
        w.writerows(report)

    with open(os.path.join(out, "gdpval_tasks.jsonl"), "w") as f:
        for conv in conversions:
            f.write(json.dumps(conv.to_gdpval_row()) + "\n")

    manifest = _build_manifest(
        bundle_dir=bundle_dir,
        bundle_name=bundle_name,
        mode=mode,
        category_from=category_from,
        occupation_maps=maps_loaded,
        layouts=layouts,
        report=report,
        conversions=conversions,
        n_source_tasks=n_source_tasks,
    )
    _write_runner(out)
    manifest["gdpval_dataset"] = "gdpval_tasks.jsonl"
    manifest["runner"] = "stirrup_runner.py"
    _write_ship_notes(out, bundle_name, bundle_dir, manifest)
    manifest["ship_notes"] = "SHIP_NOTES.md"
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return ExportResult(out_dir=out, manifest=manifest, report_rows=report)


def _build_manifest(
    *,
    bundle_dir: str,
    bundle_name: str,
    mode: str,
    category_from: str | None,
    occupation_maps: list[str],
    layouts: dict[str, int],
    report: list[dict[str, object]],
    conversions: list[TaskConversion],
    n_source_tasks: int,
) -> dict[str, object]:
    clean = sum(1 for r in report if not r["flags"])
    unmapped = sorted(
        {c.task_id for c in conversions if UNMAPPED in (c.occupation, c.sector)}
    )
    flag_keys = _FLAG_SUMMARY_KEYS + (_SPLIT_FLAG_SUMMARY_KEYS if mode == MODE_SPLIT else ())
    manifest: dict[str, object] = dict(
        converter_version=__version__,
        mode=mode,
        source_bundle=os.path.abspath(bundle_dir),
        bundle_name=bundle_name,
        converted_at=datetime.datetime.now().isoformat(timespec="seconds"),
        layouts=layouts,
        category_source=os.path.abspath(category_from) if category_from else None,
        occupation_maps=occupation_maps,
        source_tasks=n_source_tasks,
        tasks=len(report),
        clean=clean,
        flagged=len(report) - clean,
        unmapped_occupations=unmapped,
        flag_summary={
            f: sum(1 for r in report if f in str(r["flags"])) for f in flag_keys
        },
    )
    manifest["occupations"] = {
        o: sum(1 for c in conversions if c.occupation == o)
        for o in sorted({c.occupation for c in conversions})
    }
    manifest["sectors"] = {
        s: sum(1 for c in conversions if c.sector == s)
        for s in sorted({c.sector for c in conversions})
    }
    manifest["rubrics_present"] = sum(
        1 for c in conversions if c.rubric.pretty and c.rubric.json_text
    )
    manifest["msg_converted_to_txt"] = sum(len(c.converted_rels) for c in conversions)
    if conversions:
        manifest["prompt_chars"] = {
            "min": min(len(c.prompt) for c in conversions),
            "max": max(len(c.prompt) for c in conversions),
            "gdpval_band": f"{PROMPT_CHARS_MIN}-{PROMPT_CHARS_MAX}",
            "outside_band": sum(
                1
                for c in conversions
                if not (PROMPT_CHARS_MIN <= len(c.prompt) <= PROMPT_CHARS_MAX)
            ),
        }
    manifest["refs_over_gdpval_max_17"] = sum(1 for c in conversions if len(c.refs) > 17)
    manifest["rubric_residual_coupling"] = {
        "tasks": sum(1 for c in conversions if c.rubric.residual),
        "mentions": sum(len(c.rubric.residual) for c in conversions),
    }
    manifest["gold_alt_quarantined"] = sum(len(c.gold_alt) for c in conversions)
    if mode == MODE_SPLIT:
        manifest["split"] = {
            "source_tasks": n_source_tasks,
            "tasks_out": len(conversions),
            "passed_through_unsplit": sum(1 for c in conversions if c.parent_task_id is None),
            "children": sum(1 for c in conversions if c.parent_task_id is not None),
            "interdependent_flagged": sum(
                1
                for c in conversions
                if any("may be interdependent" in f for f in c.flags)
            ),
        }
    return manifest


def _write_ship_notes(
    out: str, bundle_name: str, bundle_dir: str, manifest: dict[str, object]
) -> None:
    occupations = manifest["occupations"]
    sectors = manifest["sectors"]
    prompt_chars = manifest.get(
        "prompt_chars", {"min": 0, "max": 0, "outside_band": 0}
    )
    assert isinstance(occupations, dict) and isinstance(sectors, dict)
    assert isinstance(prompt_chars, dict)
    mode = manifest["mode"]
    mode_line = ""
    if mode == MODE_SPLIT:
        split = manifest["split"]
        assert isinstance(split, dict)
        mode_line = (
            f"\nThis is a **split-by-deliverable** export: {split['tasks_out']} tasks derived "
            f"from {split['source_tasks']} source tasks ({split['children']} split children, "
            f"{split['passed_through_unsplit']} passed through unsplit, "
            f"{split['interdependent_flagged']} flagged possibly interdependent). Children "
            "share their parent's references and prompt; only the scope note and the gold "
            "slice differ. Review interdependence flags before shipping.\n"
        )
    ship = f"""# Ship notes — {bundle_name}

Converter {manifest["converter_version"]} (mode: {mode}). Source bundle: `{os.path.abspath(bundle_dir)}`.
Generated {manifest["converted_at"]}. Read this before sending the export anywhere.

## What this is

{manifest["tasks"]} tasks converted from a WorkBench bundle into the GDPval task
format: `gdpval_tasks.jsonl` carries the openai/gdpval fields (`task_id`, `sector`,
`occupation`, `prompt`, `reference_files`, `deliverable_files`, `rubric_pretty`,
`rubric_json`). Each task dir holds `references/`, `gold/` (the graded expert
deliverable), `gold_alt/` (quarantined, not graded), `rubric_source.md` (the
un-decoupled rubric, for audit) and `task.json` (conversion provenance).
Run with `stirrup_runner.py`; grading is pairwise, outside this export.
{mode_line}
## The comparability limit — state this to any recipient

GDPval-AA v2 is a **closed 220-task benchmark** whose Elo scale is anchored to
its own human expert deliverables at 1000. These {manifest["tasks"]} tasks are not
part of that set and **cannot produce a number comparable to a published
GDPval-AA v2 Elo.** They can reproduce the methodology as a private evaluation,
anchored on the goldens in `gold/`, but the resulting rating stands alone.

Do not pool these into one Bradley-Terry fit with the 220. They share a single
client engagement, a single data-room universe and a single gold author; many are
chained on upstream gold; and the harness runs 1 repeat per task, so nothing
averages out per-task noise. Pooled, they would tighten the confidence intervals
beyond what the evidence supports and over-weight one scenario.

Coverage is depth, not breadth: {len(occupations)} occupations across
{len(sectors)} sectors, against GDPval's 44 and 9.

## Known divergences from the GDPval distribution

- **Prompt length**: GDPval runs 617–6,620 chars. Ours run
  {prompt_chars["min"]:,}–{prompt_chars["max"]:,};
  {prompt_chars["outside_band"]} sit outside the band. Cause is by
  design — briefs are inlined verbatim, because the conversion relocates the
  expert's words and never rewrites them. Disclosed, not edited.
- **Reference count**: GDPval allows 0–17. {manifest["refs_over_gdpval_max_17"]}
  tasks exceed 17.
- **Occupation tags** come from a mapping, not from the task authors. Review
  `occupation_review.csv` in the bundle root before relying on any
  per-occupation breakdown.

## Open review items

- **Rubrics**: all {manifest["rubrics_present"]} carry a rubric, de-coupled from the
  WorkBench harness (agent transcript / report.json / "the environment" removed).
  {manifest["rubric_residual_coupling"]["tasks"]} tasks retain
  {manifest["rubric_residual_coupling"]["mentions"]} residual mentions that were
  **flagged rather than edited** — see `rubric_residual_coupling` in each
  `task.json`. Note "transcript" is ambiguous in some bundles: interview
  transcripts are domain content, not harness plumbing.
- **Per-task flags**: see `conversion_report.csv`. Flags are review queues, not
  errors.
- **Dangling folder pointers**: where a prompt names a folder that conversion
  emptied or the source renamed, it is flagged, never rewritten.

## Provenance

`manifest.json` is the audit trail — converter version, mode, source path, counts,
flag summary, occupation/sector spread. Keep it with anything shipped.
"""
    with open(os.path.join(out, "SHIP_NOTES.md"), "w") as f:
        f.write(ship)


_RUNNER = '''#!/usr/bin/env python3
"""Run this export's tasks through the Stirrup agent harness.

    python3 stirrup_runner.py [--model MODEL] [--task ME-A-01-P20] [--max-turns N]

Deliverables land in ./runs/<model>/<task_id>/. Reference files are uploaded as a
DIRECTORY so the nested 01_brief/ 02_handoffs/ 03_data/ tree survives - E2B's
uploader preserves structure for directories but flattens a file list to basenames.
Nothing is graded here; grading is pairwise against gold/ outside this script.
"""
import argparse, asyncio, json, os
from stirrup import Agent
from stirrup.clients.chat_completions_client import ChatCompletionsClient

HERE = os.path.dirname(os.path.abspath(__file__))

def load(task=None):
    rows = [json.loads(l) for l in open(os.path.join(HERE, 'gdpval_tasks.jsonl'))]
    return [r for r in rows if not task or r['task_id'] == task]

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default='anthropic/claude-opus-5')
    ap.add_argument('--base-url', default='https://openrouter.ai/api/v1')
    ap.add_argument('--task')
    ap.add_argument('--max-turns', type=int, default=60)
    a = ap.parse_args()

    rows = load(a.task)
    print(f'{len(rows)} task(s) -> {a.model}')
    for r in rows:
        out = os.path.join(HERE, 'runs', a.model.replace('/', '_'), r['task_id'])
        os.makedirs(out, exist_ok=True)
        refs = os.path.join(HERE, r['task_dir'], r['input_dir'])
        client = ChatCompletionsClient(base_url=a.base_url, model=a.model,
                                       max_tokens=32_000, context_window_tokens=1_000_000)
        agent = Agent(client=client, name='gdpval', max_turns=a.max_turns)
        async with agent.session(input_files=refs, output_dir=out) as session:
            await session.run(r['prompt'])
        made = sorted(os.listdir(out))
        want = [os.path.basename(d) for d in r['deliverable_files']]
        print(f"  {r['task_id']}: produced {made}")
        missing = [w for w in want if w not in made]
        if missing:
            print(f"    !! expected-but-missing: {missing}")

if __name__ == '__main__':
    asyncio.run(main())
'''


def _write_runner(out: str) -> None:
    path = os.path.join(out, "stirrup_runner.py")
    with open(path, "w") as f:
        f.write(_RUNNER)
    os.chmod(path, 0o755)
