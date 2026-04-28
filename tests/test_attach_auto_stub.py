"""Tests for the auto-stub of a project note on ``lore attach accept|manual``.

Phase 3 wiring: when an attach succeeds, ``_maybe_stub_project_note`` runs
and `_print_post_attach_guidance` mentions the result via a quiet sub-bullet
*only when a NEW note was created* (idempotent re-stubs are silent).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lore_cli.__main__ import app
from lore_core.schema import parse_frontmatter

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lore_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """LORE_ROOT with one wiki + a repo with CLAUDE.md and README."""
    lore_root = tmp_path / "lore"
    (lore_root / "wiki" / "private").mkdir(parents=True)
    (lore_root / ".lore").mkdir()
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "# My Repo\n\n"
        "A real description sentence with enough characters to qualify as the project description.\n"
    )
    (repo / "CLAUDE.md").write_text(
        "# CLAUDE.md\n\nUse the existing helpers; never reimplement.\n"
    )
    return {
        "lore_root": lore_root,
        "wiki_root": lore_root / "wiki" / "private",
        "repo": repo,
    }


# ---------------------------------------------------------------------------
# Manual attach (the simpler path)
# ---------------------------------------------------------------------------


def test_manual_attach_creates_project_note(lore_env: dict) -> None:
    result = runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--cwd",
            str(lore_env["repo"]),
            "--wiki",
            "private",
            "--scope",
            "private",
        ],
    )
    assert result.exit_code == 0, result.output

    # Project note got stubbed under wiki/private/projects/<repo-name>.md.
    projects = lore_env["wiki_root"] / "projects"
    notes = list(projects.glob("*.md"))
    assert len(notes) == 1
    fm = parse_frontmatter(notes[0].read_text())
    assert fm["type"] == "project"
    assert "description" in fm
    body = notes[0].read_text()
    assert "## Overview" in body
    assert "## Conventions" in body


def test_manual_attach_announces_new_stub(lore_env: dict) -> None:
    result = runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--cwd",
            str(lore_env["repo"]),
            "--wiki",
            "private",
            "--scope",
            "private",
        ],
    )
    assert result.exit_code == 0
    assert "Auto-stubbed project note" in result.output


def test_manual_attach_silent_on_re_attach(lore_env: dict) -> None:
    """Re-attaching the same repo refreshes the note silently — no sub-bullet."""
    runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--cwd",
            str(lore_env["repo"]),
            "--wiki",
            "private",
            "--scope",
            "private",
        ],
    )
    second = runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--cwd",
            str(lore_env["repo"]),
            "--wiki",
            "private",
            "--scope",
            "private",
        ],
    )
    assert second.exit_code == 0
    assert "Auto-stubbed" not in second.output


def test_manual_attach_does_not_fail_on_stub_error(
    lore_env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the stub generator raises, attach still succeeds (defensive) — but
    the user gets a visible-but-quiet "skipped" hint so the failure isn't
    completely silent. Bug surface, not silent rug-pull."""

    def _boom(**kwargs):
        raise RuntimeError("simulated stub failure")

    monkeypatch.setattr(
        "lore_core.projects.stub_generator.stub_project_note", _boom
    )
    result = runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--cwd",
            str(lore_env["repo"]),
            "--wiki",
            "private",
            "--scope",
            "private",
        ],
    )
    assert result.exit_code == 0
    assert "Attached" in result.output
    # No "Auto-stubbed" success line, but DO surface the skipped hint.
    assert "Auto-stubbed" not in result.output
    assert "stub skipped" in result.output
    assert "RuntimeError" in result.output


def test_manual_attach_repo_with_no_remote(lore_env: dict) -> None:
    """Repo without git origin → falls back to directory name as slug."""
    # repo dir is not a git repo at all in the fixture; current_repo returns
    # None and the fallback uses repo.name.
    result = runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--cwd",
            str(lore_env["repo"]),
            "--wiki",
            "private",
            "--scope",
            "private",
        ],
    )
    assert result.exit_code == 0
    notes = list((lore_env["wiki_root"] / "projects").glob("*.md"))
    assert len(notes) == 1
    assert notes[0].stem == lore_env["repo"].name  # fell back to dir name


# ---------------------------------------------------------------------------
# Accept-from-offer path
# ---------------------------------------------------------------------------


def test_accept_offer_creates_project_note(lore_env: dict) -> None:
    """Drop a .lore.yml in the repo root and run `lore attach accept`."""
    (lore_env["repo"] / ".lore.yml").write_text(
        "wiki: private\nscope: private\n"
    )
    result = runner.invoke(
        app,
        ["attach", "accept", "--cwd", str(lore_env["repo"])],
    )
    assert result.exit_code == 0, result.output
    assert "Auto-stubbed project note" in result.output
    notes = list((lore_env["wiki_root"] / "projects").glob("*.md"))
    assert len(notes) == 1


# ---------------------------------------------------------------------------
# Stub content carries scope from the attachment
# ---------------------------------------------------------------------------


def test_stub_records_scope_in_frontmatter(lore_env: dict) -> None:
    runner.invoke(
        app,
        [
            "attach",
            "manual",
            "--cwd",
            str(lore_env["repo"]),
            "--wiki",
            "private",
            "--scope",
            "private:dev",
        ],
    )
    notes = list((lore_env["wiki_root"] / "projects").glob("*.md"))
    assert len(notes) == 1
    fm = parse_frontmatter(notes[0].read_text())
    assert fm["scope"] == "private:dev"
