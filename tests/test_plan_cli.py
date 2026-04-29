"""Tests for ``lore plan`` CLI: list / delete / import / step / advance.

Uses typer.CliRunner for end-to-end CLI invocation. Covers the
documented behaviours from the implementation plan:

* ``list`` enumerates active plans with done/in-progress counts.
* ``delete`` refuses on incoming wikilinks without ``--force``.
* ``delete`` confirms only on ``status: active`` (skipped via stdin).
* ``import`` dispatches by extension; ambiguous → hard error.
* ``import --from-orphan`` recovers an orphan-dumped JSON envelope.
* ``import --from-markdown`` ingests raw plan markdown.
* ``step`` mutates step_status with mutual-exclusion on the flags.
* ``advance`` returns the right transition + handles "all done".
"""
from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.plans.parser import parse
from lore_core.plans.writer import compute_source_hash, plan_path, write_plan_note

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    # Click 8.2+ removed the mix_stderr kwarg; stderr now captures to its
    # own attribute by default. We only assert text-substring presence
    # via _err_or_out below, so the difference is invisible to the test.
    runner = CliRunner()


def _err_or_out(result) -> str:
    """Return whichever stream carries the error message for this click version."""
    err = getattr(result, "stderr", "") or ""
    return err + result.output


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lore_with_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """LORE_ROOT with one wiki and one fresh plan ready to mutate."""
    lore_root = tmp_path / "lore"
    wiki_root = lore_root / "wiki" / "private"
    wiki_root.mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    plan_text = (
        "# Refactor auth\n\n"
        "## Steps\n\n"
        "### Step 1: alpha\na\n\n"
        "### Step 2: beta\nb\n\n"
        "### Step 3: gamma\nc\n"
    )
    plan = parse(plan_text)
    write_plan_note(
        wiki_root=wiki_root,
        plan=plan,
        source_hash=compute_source_hash(plan_text),
        source_adapter="claude-code-hook",
        repo="lore",
    )
    return {
        "lore_root": lore_root,
        "wiki_root": wiki_root,
        "slug": "refactor-auth",
        "plan_text": plan_text,
    }


# ---------------------------------------------------------------------------
# `lore plan list`
# ---------------------------------------------------------------------------


def test_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lore_root = tmp_path / "lore"
    (lore_root / "wiki" / "x").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    result = runner.invoke(app, ["plan", "list"])
    assert result.exit_code == 0
    assert "no active plans" in result.stdout


def test_list_shows_step_status(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "list"])
    assert result.exit_code == 0
    assert "refactor-auth" in result.stdout
    assert "0/3 done" in result.stdout
    assert "next: step-1" in result.stdout


def test_list_json(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema"] == "lore.plan.list/1"
    assert len(payload["data"]["plans"]) == 1
    plan = payload["data"]["plans"][0]
    assert plan["slug"] == "refactor-auth"
    assert plan["steps_total"] == 3
    assert plan["steps_done"] == 0
    assert plan["next_pending_step"] == "step-1"


def test_list_repo_filter(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "list", "--repo", "lore"])
    assert "refactor-auth" in result.stdout
    other = runner.invoke(app, ["plan", "list", "--repo", "nope"])
    # repo filter excludes the lore-tagged plan; wiki-general fallback is
    # empty → "no active plans for repo `nope`".
    assert "no active plans" in other.stdout


# ---------------------------------------------------------------------------
# `lore plan step` + `lore plan advance`
# ---------------------------------------------------------------------------


def test_step_mark_done(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "step", "refactor-auth", "step-1", "--done"])
    assert result.exit_code == 0
    assert "step-1: pending → done" in result.stdout


def test_step_requires_exactly_one_flag(lore_with_plan: dict) -> None:
    """Zero or two+ status flags → exit 2 (the message goes to stderr;
    CliRunner doesn't always capture print(..., file=sys.stderr), so we
    pin only the exit code)."""
    result = runner.invoke(app, ["plan", "step", "refactor-auth", "step-1"])
    assert result.exit_code == 2

    result = runner.invoke(
        app, ["plan", "step", "refactor-auth", "step-1", "--done", "--in-progress"]
    )
    assert result.exit_code == 2


def test_step_unknown_id_errors(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "step", "refactor-auth", "step-99", "--done"])
    assert result.exit_code == 1
    assert "not in plan" in _err_or_out(result)


def test_step_pending_clears_status(lore_with_plan: dict) -> None:
    runner.invoke(app, ["plan", "step", "refactor-auth", "step-1", "--done"])
    result = runner.invoke(app, ["plan", "step", "refactor-auth", "step-1", "--pending"])
    assert result.exit_code == 0
    assert "step-1: done → pending" in result.stdout


def test_advance_marks_first_pending(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "advance", "refactor-auth"])
    assert result.exit_code == 0
    assert "step-1: pending → done" in result.stdout


def test_advance_picks_in_progress_first(lore_with_plan: dict) -> None:
    runner.invoke(app, ["plan", "step", "refactor-auth", "step-2", "--in-progress"])
    result = runner.invoke(app, ["plan", "advance", "refactor-auth"])
    assert "step-2: in_progress → done" in result.stdout


def test_advance_returns_when_all_done(lore_with_plan: dict) -> None:
    for sid in ("step-1", "step-2", "step-3"):
        runner.invoke(app, ["plan", "step", "refactor-auth", sid, "--done"])
    result = runner.invoke(app, ["plan", "advance", "refactor-auth"])
    assert result.exit_code == 0
    assert "nothing to advance" in result.stdout


# ---------------------------------------------------------------------------
# `lore plan delete`
# ---------------------------------------------------------------------------


def test_delete_refuses_on_incoming_wikilinks(lore_with_plan: dict) -> None:
    """A session note linking to plan/<slug>#s2 must block delete without --force."""
    sessions = lore_with_plan["wiki_root"] / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "x.md").write_text(
        "---\ntype: session\n---\n\nWorking on [[plan/refactor-auth#s2]]\n"
    )

    result = runner.invoke(app, ["plan", "delete", "refactor-auth"])
    assert result.exit_code == 2
    assert "wikilink" in _err_or_out(result)
    # Plan still exists.
    assert plan_path(lore_with_plan["wiki_root"], "refactor-auth").exists()


def test_delete_force_overrides_incoming_wikilinks(lore_with_plan: dict) -> None:
    sessions = lore_with_plan["wiki_root"] / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "x.md").write_text(
        "---\ntype: session\n---\n\nWorking on [[plan/refactor-auth#s2]]\n"
    )

    result = runner.invoke(app, ["plan", "delete", "refactor-auth", "--force"])
    assert result.exit_code == 0
    assert "deleted" in result.stdout
    assert not plan_path(lore_with_plan["wiki_root"], "refactor-auth").exists()


def test_delete_active_status_in_non_interactive_aborts(
    lore_with_plan: dict,
) -> None:
    """No incoming wikilinks but status=active + non-interactive → silent default 'no'."""
    result = runner.invoke(app, ["plan", "delete", "refactor-auth"])
    assert result.exit_code == 1
    assert "aborted" in _err_or_out(result)
    assert plan_path(lore_with_plan["wiki_root"], "refactor-auth").exists()


def test_delete_force_active_status(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "delete", "refactor-auth", "--force"])
    assert result.exit_code == 0
    assert not plan_path(lore_with_plan["wiki_root"], "refactor-auth").exists()


def test_delete_done_status_no_confirm(lore_with_plan: dict) -> None:
    """status=done → friction-free delete, no --force needed."""
    plan_file = plan_path(lore_with_plan["wiki_root"], "refactor-auth")
    plan_file.write_text(
        plan_file.read_text().replace("status: active", "status: done")
    )
    result = runner.invoke(app, ["plan", "delete", "refactor-auth"])
    assert result.exit_code == 0


def test_delete_unknown_slug_errors(lore_with_plan: dict) -> None:
    result = runner.invoke(app, ["plan", "delete", "nonexistent"])
    assert result.exit_code == 1
    assert "not found" in _err_or_out(result)


# ---------------------------------------------------------------------------
# `lore plan import`
# ---------------------------------------------------------------------------


def test_import_from_markdown_dispatches_by_extension(
    lore_with_plan: dict, tmp_path: Path
) -> None:
    """Bare `lore plan import path.md` → from-markdown."""
    md_file = tmp_path / "historical.md"
    md_file.write_text(
        "# Historical plan\n\n## Steps\n\n### Step 1: foo\nx\n\n### Step 2: bar\ny\n"
    )
    result = runner.invoke(app, ["plan", "import", str(md_file)])
    assert result.exit_code == 0
    imported = lore_with_plan["wiki_root"] / "plans" / "historical-plan.md"
    assert imported.exists()


def test_import_from_orphan_recovers_envelope(
    lore_with_plan: dict, tmp_path: Path
) -> None:
    """Orphan JSON envelope → ingests via parse_payload."""
    orphan = tmp_path / "orphan.json"
    orphan.write_text(
        json.dumps({
            "tool_input": {"plan": "# Recovered\n\n1. one\n2. two\n3. three\n"},
            "tool_response": {"approved": True},
        })
    )
    result = runner.invoke(app, ["plan", "import", str(orphan)])
    assert result.exit_code == 0
    imported = lore_with_plan["wiki_root"] / "plans" / "recovered.md"
    assert imported.exists()


def test_import_explicit_mode_overrides_extension(
    lore_with_plan: dict, tmp_path: Path
) -> None:
    """If user passes both .json AND --from-markdown, treat as markdown."""
    weird = tmp_path / "looks-json.json"
    weird.write_text("# Plain markdown despite extension\n\n1. one\n2. two\n")
    result = runner.invoke(
        app, ["plan", "import", str(weird), "--from-markdown"]
    )
    assert result.exit_code == 0


def test_import_ambiguous_extension_errors(
    lore_with_plan: dict, tmp_path: Path
) -> None:
    weird = tmp_path / "noext"
    weird.write_text("some content")
    result = runner.invoke(app, ["plan", "import", str(weird)])
    assert result.exit_code == 2
    assert "ambiguous" in _err_or_out(result)


def test_import_both_flags_errors(lore_with_plan: dict, tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text("x")
    result = runner.invoke(
        app, ["plan", "import", str(f), "--from-orphan", "--from-markdown"]
    )
    assert result.exit_code == 2


def test_import_missing_file_errors(lore_with_plan: dict, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["plan", "import", str(tmp_path / "missing.md")]
    )
    assert result.exit_code == 1
    assert "not found" in _err_or_out(result)


def test_import_orphan_with_no_plan_errors(
    lore_with_plan: dict, tmp_path: Path
) -> None:
    orphan = tmp_path / "empty.json"
    orphan.write_text(json.dumps({"tool_input": {"id": "x"}}))
    result = runner.invoke(app, ["plan", "import", str(orphan)])
    assert result.exit_code == 1
    assert "no plan" in _err_or_out(result).lower()
