"""wb2gdpval command line.

    wb2gdpval convert <bundle_dir> [-o EXPORTS_ROOT] [--mode one2one|split] ...
    wb2gdpval check <export_dir> --bundle <bundle_dir> [--mode ...]
    wb2gdpval rlgen generate <task_dir> [-o RL_OUT] [-n N] [--model ...]
    wb2gdpval rlgen validate <candidates_dir> [--refdir DIR]
    wb2gdpval rlgen export <candidates_dir> [-o ENVS_ROOT]
    wb2gdpval rlgen gate <envs_root> [--model ...] [--threshold X] [--no-discard]

Exit status is non-zero when acceptance checks fail, so conversions can gate CI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .acceptance import AcceptanceReport, check_export
from .config import BundleConfig, load_bundle_config
from .export import MODE_ONE2ONE, MODES, run_export

DEFAULT_CONFIG = os.path.join("configs", "bundles.yaml")


def _print_acceptance(report: AcceptanceReport) -> None:
    for note in report.notes:
        print(f"note: {note}")
    for failure in report.failures:
        print(f"ACCEPTANCE FAILURE: {failure}", file=sys.stderr)
    print("acceptance: " + ("OK" if report.ok else f"{len(report.failures)} failure(s)"))


def _load_config(args: argparse.Namespace, bundle_dir: str) -> BundleConfig | None:
    return load_bundle_config(args.config, bundle_dir)


def _cmd_convert(args: argparse.Namespace) -> int:
    cfg = _load_config(args, args.bundle_dir)
    category_from = args.category_from or (cfg.category_from if cfg else None)
    occupation_map = args.occupation_map or (cfg.occupation_map if cfg else None)
    task_glob = args.task_glob or (cfg.task_glob if cfg else "ME-*")
    result = run_export(
        bundle_dir=args.bundle_dir,
        exports_root=args.out,
        mode=args.mode,
        category_from=category_from,
        occupation_map=occupation_map,
        task_glob=task_glob,
    )
    manifest = result.manifest
    print(f"output: {result.out_dir}")
    print(
        f"{manifest['tasks']} tasks converted; {manifest['clean']} clean, "
        f"{manifest['flagged']} flagged"
    )
    for row in result.report_rows:
        if row["flags"]:
            print(f"  {row['task']}: {row['flags']}")
    if args.no_check:
        return 0
    report = check_export(result.out_dir, cfg, args.mode)
    _print_acceptance(report)
    return 0 if report.ok else 1


def _cmd_check(args: argparse.Namespace) -> int:
    cfg = _load_config(args, args.bundle)
    report = check_export(args.export_dir, cfg, args.mode)
    _print_acceptance(report)
    return 0 if report.ok else 1


def _cmd_rlgen_generate(args: argparse.Namespace) -> int:
    from .rlgen.generate import generate_candidates
    from .rlgen.llm import LLMClient, LLMConfig

    config = LLMConfig()
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    result = generate_candidates(
        task_dir=args.task_dir,
        out_dir=args.out,
        n=args.n,
        client=LLMClient(config),
        category_from=args.category_from,
    )
    print(
        f"{len(result.candidates)} candidate(s) -> {os.path.join(args.out, result.source_task_id)}"
    )
    print("next: wb2gdpval rlgen validate " + os.path.join(args.out, result.source_task_id))
    return 0


def _cmd_rlgen_validate(args: argparse.Namespace) -> int:
    from .rlgen.validate import validate_dir

    summary = validate_dir(args.candidates_dir, refdir=args.refdir)
    print(json.dumps(summary, indent=1))
    if summary["valid"] == 0:
        print("no candidate survived validation", file=sys.stderr)
        return 1
    print("next: wb2gdpval rlgen export " + args.candidates_dir)
    return 0


def _cmd_rlgen_export(args: argparse.Namespace) -> int:
    from .rlgen.export_inspect import export_env
    from .rlgen.schema import CandidateEnv
    from .rlgen.validate import validate_dir

    validated_path = os.path.join(args.candidates_dir, "validated.json")
    if not os.path.isfile(validated_path):
        print("no validated.json — running validation first")
        validate_dir(args.candidates_dir, refdir=args.refdir)
    with open(validated_path) as f:
        candidates = [CandidateEnv.from_dict(d) for d in json.load(f)]
    refdir = args.refdir
    if refdir is None:
        with open(os.path.join(args.candidates_dir, "generation_meta.json")) as f:
            refdir = json.load(f)["refdir"]
    for c in candidates:
        dest = export_env(c, refdir, args.out)
        print(f"exported {c.env_id} -> {dest}")
    if candidates:
        print("next: wb2gdpval rlgen gate " + args.out)
    else:
        print("nothing to export", file=sys.stderr)
        return 1
    return 0


def _cmd_rlgen_gate(args: argparse.Namespace) -> int:
    from .rlgen.difficulty import gate_all

    results = gate_all(
        args.envs_root,
        model=args.model,
        solve_threshold=args.threshold,
        discard=not args.no_discard,
    )
    for r in results:
        status = (
            "ERROR " + (r.error or "")
            if r.error
            else (
                "DISCARDED (solved first try)"
                if r.discarded
                else "solved (kept)"
                if r.solved
                else "kept"
            )
        )
        score = f"{r.score:.2f}" if r.score is not None else "-"
        print(f"  {r.env_id}: score={score} {status}")
    kept = sum(1 for r in results if not r.discarded and not r.error)
    print(f"{len(results)} gated; {kept} kept")
    return 0 if results else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wb2gdpval", description=__doc__)
    p.add_argument("--version", action="version", version=f"wb2gdpval {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    conv = sub.add_parser("convert", help="convert a WorkBench bundle to GDPval format")
    conv.add_argument("bundle_dir")
    conv.add_argument("-o", "--out", default="./exports", help="exports root directory")
    conv.add_argument("--mode", choices=MODES, default=MODE_ONE2ONE)
    conv.add_argument("--config", default=DEFAULT_CONFIG, help="bundles.yaml path")
    conv.add_argument("--category-from", help="pack-layout bundle to join category from")
    conv.add_argument("--occupation-map", help="extra occupation_map.json")
    conv.add_argument("--task-glob", help="glob for task dirs inside the bundle")
    conv.add_argument("--no-check", action="store_true", help="skip acceptance checks")
    conv.set_defaults(func=_cmd_convert)

    chk = sub.add_parser("check", help="run acceptance checks on an existing export")
    chk.add_argument("export_dir")
    chk.add_argument("--bundle", required=True, help="source bundle dir (config key)")
    chk.add_argument("--mode", choices=MODES, default=MODE_ONE2ONE)
    chk.add_argument("--config", default=DEFAULT_CONFIG)
    chk.set_defaults(func=_cmd_check)

    rl = sub.add_parser("rlgen", help="Mode C2: RL environments with programmatic rewards")
    rlsub = rl.add_subparsers(dest="rl_command", required=True)

    g = rlsub.add_parser("generate", help="propose candidate envs from one source task")
    g.add_argument("task_dir", help="one WorkBench task directory")
    g.add_argument("-o", "--out", default="./rl_out")
    g.add_argument("-n", type=int, default=3, help="candidates to request")
    g.add_argument("--model", help="generator model id")
    g.add_argument("--base-url", help="OpenAI-compatible endpoint")
    g.add_argument("--category-from")
    g.set_defaults(func=_cmd_rlgen_generate)

    v = rlsub.add_parser("validate", help="verify every asserted fact against the files")
    v.add_argument("candidates_dir", help="rl_out/<task_id> directory")
    v.add_argument("--refdir", help="override the reference dir recorded at generation")
    v.set_defaults(func=_cmd_rlgen_validate)

    e = rlsub.add_parser("export", help="export validated candidates as inspect_ai tasks")
    e.add_argument("candidates_dir")
    e.add_argument("-o", "--out", default="./rl_out/envs")
    e.add_argument("--refdir")
    e.set_defaults(func=_cmd_rlgen_export)

    ga = rlsub.add_parser("gate", help="difficulty gate: discard envs solved first try")
    ga.add_argument("envs_root")
    ga.add_argument("--model", default="anthropic/claude-opus-5", help="attacker model")
    ga.add_argument("--threshold", type=float, default=0.8, help="solve threshold")
    ga.add_argument("--no-discard", action="store_true", help="report only, keep all")
    ga.set_defaults(func=_cmd_rlgen_gate)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
