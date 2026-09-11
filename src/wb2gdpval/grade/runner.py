"""Worker runs: models attempt the export's tasks in a Docker sandbox.

Deliverables land in ``<export>/runs/<model>/<task_id>/``, collected by a
scorer while the sandbox is still alive (there is no sandbox after the eval
ends). Runs are resumable: a task dir that already contains files is skipped.

Requires the ``inspect`` extra, Docker, and provider credentials in the
environment (inspect-ai naming, e.g. ``openrouter/anthropic/claude-opus-5``
with ``OPENROUTER_API_KEY`` set).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

# The sandbox image is prebuilt once (offline, from vendored wheels) and
# referenced by tag — inspect tears its per-eval images down after each eval,
# and a per-eval `build:` makes every run depend on PyPI being reachable from
# inside the Docker VM, which is exactly what broke a live run.
SANDBOX_IMAGE = "wb2gdpval-grade:latest"

_DOCKERFILE = """FROM python:3.12-slim
COPY wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels \\
    python-docx \\
    python-pptx \\
    openpyxl \\
    pandas \\
    markdown \\
 && rm -rf /wheels
WORKDIR /task
RUN mkdir -p /task/reference /task/output
"""

_COMPOSE = f"""services:
  default:
    image: {SANDBOX_IMAGE}
    working_dir: /task
    command: tail -f /dev/null
    init: true
"""

# Populate the wheels dir (once, on the host network) with:
#   pip download python-docx python-pptx openpyxl pandas markdown \
#     -d <sandbox>/wheels --only-binary=:all: \
#     --platform manylinux2014_aarch64 --python-version 3.12
_WHEELS_HELP = (
    "sandbox image missing and no vendored wheels to build it from; "
    "populate {wheels} with `pip download python-docx python-pptx openpyxl "
    "pandas markdown -d {wheels} --only-binary=:all: "
    "--platform manylinux2014_aarch64 --python-version 3.12` "
    "(use a PyPI mirror via -i if files.pythonhosted.org is unreachable)"
)

_SYSTEM_MESSAGE = (
    "You are completing a professional work assignment. The reference files are in "
    "/task/reference. Produce every requested deliverable, under exactly the "
    "requested filename, in /task/output."
)

OUTPUT_DIR = "/task/output"


@dataclass
class RunResult:
    task_id: str
    model: str
    produced: list[str]
    missing: list[str]
    error: str | None = None
    skipped: bool = False


def _model_dirname(model: str) -> str:
    return model.replace("/", "_")


def _sandbox_dir(export_dir: str) -> str:
    d = os.path.join(export_dir, ".grade_sandbox")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "Dockerfile"), "w") as f:
        f.write(_DOCKERFILE)
    with open(os.path.join(d, "compose.yaml"), "w") as f:
        f.write(_COMPOSE)
    return d


def ensure_sandbox_image(export_dir: str) -> None:
    """Build the sandbox image from vendored wheels if it is not present."""
    if (
        subprocess.run(
            ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True
        ).returncode
        == 0
    ):
        return
    d = _sandbox_dir(export_dir)
    wheels = os.path.join(d, "wheels")
    if not os.path.isdir(wheels) or not os.listdir(wheels):
        raise RuntimeError(_WHEELS_HELP.format(wheels=wheels))
    result = subprocess.run(
        ["docker", "build", "-t", SANDBOX_IMAGE, d], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"sandbox image build failed:\n{result.stderr[-2000:]}")


def load_rows(export_dir: str, task_ids: list[str] | None = None) -> list[dict[str, object]]:
    with open(os.path.join(export_dir, "gdpval_tasks.jsonl")) as f:
        rows = [json.loads(line) for line in f]
    if task_ids:
        wanted = set(task_ids)
        rows = [r for r in rows if r["task_id"] in wanted]
    return rows


def _run_one(export_dir: str, row: dict[str, object], model: str, max_messages: int) -> RunResult:
    from inspect_ai import Task
    from inspect_ai import eval as inspect_eval
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import Score, Target, mean, scorer
    from inspect_ai.solver import TaskState, basic_agent, system_message
    from inspect_ai.tool import bash, python
    from inspect_ai.util import sandbox

    task_id = str(row["task_id"])
    dest = os.path.join(export_dir, "runs", _model_dirname(model), task_id)
    deliverable_files = row["deliverable_files"]
    assert isinstance(deliverable_files, list)
    expected = [os.path.basename(str(d)) for d in deliverable_files]

    refroot = os.path.join(export_dir, str(row["task_dir"]), str(row["input_dir"]))
    files: dict[str, str] = {}
    for root, _, names in os.walk(refroot):
        for name in names:
            p = os.path.join(root, name)
            files[os.path.join("/task/reference", os.path.relpath(p, refroot))] = p

    @scorer(metrics=[mean()])
    def collect_outputs():  # type: ignore[no-untyped-def]
        async def score(state: TaskState, target: Target) -> Score:
            os.makedirs(dest, exist_ok=True)
            listing = await sandbox().exec(["ls", "-1", OUTPUT_DIR])
            names = [n for n in listing.stdout.splitlines() if n.strip()]
            for name in names:
                try:
                    data = await sandbox().read_file(os.path.join(OUTPUT_DIR, name), text=False)
                except Exception:
                    continue
                with open(os.path.join(dest, name), "wb") as fh:
                    fh.write(data)
            got = sum(1 for e in expected if e in names)
            return Score(
                value=got / len(expected) if expected else 1.0,
                explanation=f"collected {names}; expected {expected}",
            )

        return score

    task = Task(
        dataset=[Sample(id=task_id, input=str(row["prompt"]), files=files)],
        solver=basic_agent(
            init=system_message(_SYSTEM_MESSAGE),
            tools=[bash(timeout=300), python(timeout=300)],
            max_messages=max_messages,
        ),
        scorer=collect_outputs(),
        sandbox=("docker", os.path.join(_sandbox_dir(export_dir), "compose.yaml")),
        name=f"grade_{task_id}",
    )
    logs = inspect_eval(
        task,
        model=model,
        log_dir=os.path.join(export_dir, "runs", "_logs", _model_dirname(model)),
        display="plain",
    )
    log = logs[0]
    if log.status != "success":
        return RunResult(task_id, model, [], expected, error=f"eval status {log.status}")
    produced = sorted(os.listdir(dest)) if os.path.isdir(dest) else []
    missing = [e for e in expected if e not in produced]
    return RunResult(task_id, model, produced, missing)


def run_workers(
    export_dir: str,
    models: list[str],
    task_ids: list[str] | None = None,
    max_messages: int = 120,
) -> list[RunResult]:
    """Run each worker over the selected tasks; resumable per (model, task)."""
    ensure_sandbox_image(export_dir)
    rows = load_rows(export_dir, task_ids)
    results: list[RunResult] = []
    for model in models:
        for row in rows:
            task_id = str(row["task_id"])
            dest = os.path.join(export_dir, "runs", _model_dirname(model), task_id)
            if os.path.isdir(dest) and os.listdir(dest):
                results.append(
                    RunResult(task_id, model, sorted(os.listdir(dest)), [], skipped=True)
                )
                continue
            try:
                results.append(_run_one(export_dir, row, model, max_messages))
            except Exception as e:
                results.append(RunResult(task_id, model, [], [], error=f"{type(e).__name__}: {e}"))
    with open(os.path.join(export_dir, "runs", "run_report.json"), "w") as f:
        json.dump([r.__dict__ for r in results], f, indent=1)
    return results
