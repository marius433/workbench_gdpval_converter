"""Candidate generation: a model reads an ME task and proposes new RL tasks.

The generator sees the source assignment plus the *contents* of the reference
files (spreadsheets are dumped with cell addresses so checks can cite
``sheet!cell``). It is instructed to propose hard, multi-step assignments
whose rewards are computable — every numeric check must carry a derivation
and file-grounded refs, because the validator will recompute all of it and
reject anything unverifiable.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import openpyxl

from ..convert import convert_task
from ..occupations import OccupationResolver
from ..textio import docx_text, msg_text
from .llm import LLMClient, extract_json
from .schema import CANDIDATE_JSON_GUIDE, CandidateEnv

# Context budget per reference file, characters. Spreadsheet dumps are dense;
# prose files rarely need more.
_MAX_CHARS_PER_FILE = 12_000
_MAX_ROWS_PER_SHEET = 80

_SYSTEM = (
    """You design benchmark tasks for evaluating AI agents on real knowledge work.

You are given one existing consulting assignment (the "source task"): its prompt and the
full contents of its data room (the reference files). Your job is to propose NEW
assignments over the SAME data room, each one an RL environment with a programmatic
reward — graded by machine-checkable facts, not by human comparison.

Hard requirements for every candidate:
- The assignment must be answerable from the reference files alone, and must be
  DIFFERENT work from the source task (a different question, deliverable or analysis
  angle - not a paraphrase).
- BUILD EACH TASK AROUND A TRAP the data room genuinely contains: a published column
  on the wrong basis, a headline figure the raw data contradicts, superseded numbers
  a later document corrects, a definitional mismatch between files, mixed units. The
  expected value of a check must be reachable ONLY by rejecting that obvious path.
  A value that can be read off a single cell, or computed the first way anyone would
  try, is worthless as a reward.
- For at least TWO numeric checks per candidate, also supply a `decoy`: the
  derivation the obvious-but-wrong path yields (with its own grounding refs). The
  validator computes the decoy from the real files and requires it to land OUTSIDE
  the check's tolerance — if the naive path scores, the candidate is rejected.
- Every numeric check must include: a `derivation` (pure arithmetic over named refs
  and numeric literals) and `refs` grounding every input — spreadsheet values as
  {file, sheet, cell}, prose facts as {file, quote} with an EXACT substring from the
  file text you were shown. A validator will recompute every derivation against the
  real files and discard anything that does not verify, so never estimate and never
  invent cell addresses.
- SECRECY DISCIPLINE: the agent will see only the `prompt` and each check's
  `report_ask` (under a blinded key like result_01) — never your `key`,
  `description`, `derivation` or `decoy`. Therefore the prompt and every
  report_ask must define WHAT to produce and report, in the words a client would
  use, without naming the trap, the correct basis, the adjustment, or the files
  that resolve it. If your report_ask gives away the method, the task is dead on
  arrival. Set `unit` so the agent knows the unit without it leaking method.
- Do NOT tell the agent to write report.json in your prompt — the harness appends
  the report instructions itself. End the prompt at the professional assignment.
- 5 to 8 checks per candidate; include one file_exists check per deliverable.
- Tolerances: as tight as the arithmetic allows (rounding only), never wide enough
  to admit the decoy value.

Return ONLY a JSON object in this shape:
"""
    + CANDIDATE_JSON_GUIDE
)


def _user_message(task_id: str, occupation: str, prompt: str, references: list[str], n: int) -> str:
    # Assembled by f-string (not str.format) because the prompt and file dumps
    # are untrusted text that may contain brace characters.
    refs = "\n\n".join(references)
    return (
        f"SOURCE TASK {task_id} (occupation: {occupation}).\n\n"
        f"Source prompt:\n---\n{prompt}\n---\n\n"
        f"Reference files ({len(references)}), with contents:\n\n{refs}\n\n"
        f"Propose {n} candidate environments as specified. Aim for the hardest tasks "
        "the data room can support and ground every check. JSON only."
    )


def _dump_xlsx(path: str) -> str:
    """Cell-addressed dump so the model can cite sheet!cell in refs."""
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception:
        return "(workbook unreadable)"
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"# Sheet: {ws.title}")
        for r, row in enumerate(ws.iter_rows(values_only=False), start=1):
            if r > _MAX_ROWS_PER_SHEET:
                lines.append("# (sheet truncated)")
                break
            cells = [f"{c.coordinate}={c.value!r}" for c in row if c.value is not None]
            if cells:
                lines.append("  ".join(cells))
    return "\n".join(lines)


def _dump_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        text = _dump_xlsx(path)
    elif ext == ".docx":
        text = docx_text(path) or "(unreadable)"
    elif ext == ".msg":
        text = msg_text(path) or "(unreadable)"
    elif ext in (".pdf", ".pptx", ".png", ".jpg", ".zip"):
        text = "(binary file; contents not shown)"
    else:
        try:
            with open(path, errors="replace") as f:
                text = f.read()
        except Exception:
            text = "(unreadable)"
    if len(text) > _MAX_CHARS_PER_FILE:
        text = text[:_MAX_CHARS_PER_FILE] + "\n(truncated)"
    return text


@dataclass
class GenerationResult:
    source_task_id: str
    refdir: str
    candidates: list[CandidateEnv] = field(default_factory=list)
    raw_completion: str = ""


def build_context(
    task_dir: str, n: int, category_from: str | None = None
) -> tuple[str, str, dict[str, str]]:
    """Returns (task_id, user_message, meta) for one source task directory."""
    resolver = OccupationResolver()
    resolver.load_maps_near(os.path.dirname(os.path.abspath(task_dir)))
    conv = convert_task(task_dir, resolver, category_from=category_from)
    if conv is None:
        raise ValueError(f"unrecognised task layout: {task_dir}")
    ref_sections = []
    for r in conv.refs:
        body = r.msg_txt if r.msg_txt is not None else _dump_file(r.src)
        ref_sections.append(f"=== {r.rel} ===\n{body}")
    meta = {
        "task_id": conv.task_id,
        "occupation": conv.occupation,
        "sector": conv.sector,
        "refdir": os.path.join(task_dir, "reference")
        if conv.layout == "review"
        else os.path.join(task_dir, "harness", "reference"),
    }
    user = _user_message(conv.task_id, conv.occupation, conv.prompt, ref_sections, n)
    return conv.task_id, user, meta


def parse_candidates(
    completion: str, source_task_id: str, occupation: str, sector: str
) -> list[CandidateEnv]:
    """Parse and normalise the model's candidates. Malformed entries raise."""
    obj = extract_json(completion)
    out: list[CandidateEnv] = []
    for i, c in enumerate(obj.get("candidates", []), start=1):
        slug = re.sub(r"[^a-z0-9-]", "-", str(c.get("env_id", f"c{i}")).lower()).strip("-")
        c["env_id"] = f"{source_task_id}-RL-{i:02d}-{slug}"[:80]
        c.setdefault("source_task_id", source_task_id)
        c.setdefault("occupation", occupation)
        c.setdefault("sector", sector)
        out.append(CandidateEnv.from_dict(c))
    return out


def generate_candidates(
    task_dir: str,
    out_dir: str,
    n: int = 3,
    client: LLMClient | None = None,
    category_from: str | None = None,
) -> GenerationResult:
    """Run generation for one source task and persist candidates + provenance."""
    task_id, user, meta = build_context(task_dir, n, category_from=category_from)
    client = client or LLMClient()
    completion = client.complete(_SYSTEM, user)

    # Persist the raw completion before parsing: a malformed completion must
    # stay inspectable (and paid-for tokens must never be lost to a parse error).
    dest = os.path.join(out_dir, task_id)
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "raw_completion.txt"), "w") as f:
        f.write(completion)
    try:
        candidates = parse_candidates(completion, task_id, meta["occupation"], meta["sector"])
    except ValueError as e:
        raise ValueError(
            f"{e} — raw completion kept at {os.path.join(dest, 'raw_completion.txt')}"
        ) from e

    with open(os.path.join(dest, "candidates.json"), "w") as f:
        json.dump([c.to_dict() for c in candidates], f, indent=1)
    with open(os.path.join(dest, "generation_meta.json"), "w") as f:
        json.dump(
            {
                "source_task_dir": os.path.abspath(task_dir),
                "refdir": os.path.abspath(meta["refdir"]),
                "model": client.config.model,
                "base_url": client.config.base_url,
                "n_requested": n,
                "n_returned": len(candidates),
            },
            f,
            indent=1,
        )
    return GenerationResult(
        source_task_id=task_id,
        refdir=meta["refdir"],
        candidates=candidates,
        raw_completion=completion,
    )
