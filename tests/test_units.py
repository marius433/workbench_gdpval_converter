"""Unit tests for the conversion building blocks."""

from __future__ import annotations

import os

from wb2gdpval.briefs import find_briefs
from wb2gdpval.gold import select_gold
from wb2gdpval.layouts import detect_layout, md_description, normalise_tid
from wb2gdpval.occupations import OccupationResolver
from wb2gdpval.prompts import parse_deliverables
from wb2gdpval.rubric import decouple, load_rubrics
from wb2gdpval.textio import clean_email, msg_text, norm_name


def test_norm_name_is_punctuation_insensitive() -> None:
    assert norm_name("E-mail from Marcus.docx") == "emailfrommarcusdocx"
    assert "email" in norm_name("Manager_e-mail.docx")


def test_clean_email_strips_headers_and_salutation() -> None:
    # The line right after the header block (the salutation) is consumed too —
    # sanctioned 1.4.0 behaviour, see the clean_email docstring.
    text = "From: A\nTo: B\nSubject: C\n\nDear Bob,\n\nBody line one.\n\n\n\nBody line two."
    cleaned = clean_email(text)
    assert cleaned.startswith("Body line one.")
    assert "Subject" not in cleaned
    assert "Dear Bob" not in cleaned
    assert "\n\n\n" not in cleaned  # blank runs collapsed


def test_msg_text_reads_mime_despite_extension(tmp_path) -> None:
    p = tmp_path / "note.msg"
    p.write_text("From: X <x@e.com>\nSubject: Hi\nContent-Type: text/plain\n\nThe body.\n")
    text = msg_text(str(p))
    assert text is not None
    assert "From: X <x@e.com>" in text
    assert "The body." in text


def test_parse_deliverables_save_as_sentence() -> None:
    desc = (
        "Do the work.\n\nSave your deliverable(s) as `a.docx`, `b.xlsx` in your output directory."
    )
    assert parse_deliverables(desc) == ["a.docx", "b.xlsx"]


def test_parse_deliverables_fallback_excludes_report_json() -> None:
    desc = "Produce plan.docx and also write report.json alongside."
    assert parse_deliverables(desc) == ["plan.docx"]


def test_normalise_tid_strips_packaging_suffixes() -> None:
    assert normalise_tid("ME-A-01-P20_vF") == "ME-A-01-P20"
    assert normalise_tid("ME-A-01-P20_evals_2") == "ME-A-01-P20"
    assert normalise_tid("ME-A-01-P20") == "ME-A-01-P20"


def test_occupation_resolution_and_sector_pairing() -> None:
    r = OccupationResolver()
    occ, sector, source = r.resolve("ME-Z-99-P01", "strategy")
    assert occ == "Project Management Specialists"
    assert sector == "Professional, Scientific, and Technical Services"
    assert source == "category-table"
    assert r.resolve("ME-Z-99-P09", "unknown_category") == ("UNMAPPED", "UNMAPPED", "unmapped")


def test_occupation_per_task_override_wins(tmp_path) -> None:
    m = tmp_path / "occupation_map.json"
    m.write_text('{"ME-Z-99-P01": ["Financial and Investment Analysts"]}')
    r = OccupationResolver()
    r.load_map(str(m))
    occ, sector, source = r.resolve("ME-Z-99-P01", "strategy")
    assert occ == "Financial and Investment Analysts"
    assert sector == "Finance and Insurance"  # derived via GDPVAL_SECTOR
    assert source == "task-override"


def test_decouple_removes_plumbing_and_flags_residuals() -> None:
    text = (
        "The environment contains: transcript and files. "
        "Inspect the actual output and source files, not just the transcript. "
        "The memo must state the growth rate. "
        "Weigh the transcript where the memo is ambiguous."
    )
    out, resid = decouple(text)
    assert "The environment contains" not in out
    assert "Inspect the actual" not in out
    assert "The memo must state the growth rate." in out  # live criterion untouched
    assert any(r["why"] == "still refers to the agent transcript" for r in resid)


def test_gold_hygiene_quarantines_report_and_duplicate_renderings(tmp_path) -> None:
    for name in ("memo.docx", "memo.pdf", "model.xlsx", "report.json", ".DS_Store"):
        (tmp_path / name).write_text("x")
    kept, quarantined = select_gold(str(tmp_path), {"memo.docx", "model.xlsx"})
    assert kept == ["memo.docx", "model.xlsx"]
    assert quarantined == ["memo.pdf", "report.json"]


def test_detect_layouts(review_bundle: str, pack_bundle: str) -> None:
    review = detect_layout(os.path.join(review_bundle, "ME-Z-99-P01_vF"))
    assert review is not None and review.kind == "review"
    pack = detect_layout(os.path.join(pack_bundle, "ME-Z-99-P01"))
    assert pack is not None and pack.kind == "pack"
    assert detect_layout(review_bundle) is None  # bundle root is not a task


def test_md_description_excludes_system_addendum(review_bundle: str) -> None:
    desc = md_description(
        os.path.join(review_bundle, "ME-Z-99-P01_vF", "user_prompt", "user_prompt.md")
    )
    assert "quarterly memo" in desc
    assert "report.json" not in desc  # the System Addendum section is excluded


def test_find_briefs_dedups_description_and_folder(review_bundle: str) -> None:
    refdir = os.path.join(review_bundle, "ME-Z-99-P01_vF", "reference")
    desc = md_description(
        os.path.join(review_bundle, "ME-Z-99-P01_vF", "user_prompt", "user_prompt.md")
    )
    briefs = find_briefs(refdir, desc)
    assert [os.path.basename(b) for b in briefs] == ["Client_Email.docx"]


def test_load_rubrics_decouples_and_keeps_source(review_bundle: str) -> None:
    rb = load_rubrics(os.path.join(review_bundle, "ME-Z-99-P01_vF", "grader", "grading.yaml"))
    assert rb.pretty.startswith("Grading note:")
    assert "The environment contains" not in rb.pretty
    assert "The environment contains" in rb.pretty_source  # audit copy untouched
    assert rb.json_text  # llm_judge.rubric carried
    assert any("residual coupling" in f for f in rb.flags)
