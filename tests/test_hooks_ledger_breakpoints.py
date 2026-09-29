"""Breakpoint hooks name the build ledger (PRD 0015 § Ledger and breakpoints).

`lore hook pre-compact` names the ledger path; `lore hook session-start`
offers to resume from the last resume line. Neither mentions a ledger when
the worktree has none.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_cli.hooks import hook_app
from typer.testing import CliRunner

LEDGER = """\
# Lore ledger

- [adr] open — Keep risk deterministic
- [resume] 2026-09-29T09:00:00Z — round 1 merged; next: wire ledger-check
- [resume] 2026-09-29T10:00:00Z — round 2 merged; next: wire the hook
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "vault" / "wiki").mkdir(parents=True)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "vault"))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _run(*args: str) -> str:
    result = CliRunner().invoke(hook_app, list(args), input="")
    assert result.exit_code == 0, result.output
    return result.output


def test_pre_compact_names_the_ledger_path(repo: Path) -> None:
    ledger = repo / ".git" / "lore-ledger.md"
    ledger.write_text(LEDGER)
    out = _run("pre-compact", "--cwd", str(repo))
    assert str(ledger) in json.loads(out)["systemMessage"]


def test_session_start_offers_resume_from_last_resume_line(repo: Path) -> None:
    (repo / ".git" / "lore-ledger.md").write_text(LEDGER)
    out = _run("session-start", "--cwd", str(repo), "--plain", "--probe")
    assert "resume" in out.lower()
    assert "round 2 merged; next: wire the hook" in out
    assert "round 1 merged" not in out


def test_linked_worktree_ledger_is_found(repo: Path, tmp_path: Path) -> None:
    wt_gitdir = repo / ".git" / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "lore-ledger.md").write_text(LEDGER)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    out = _run("pre-compact", "--cwd", str(wt), "--plain")
    assert str(wt_gitdir / "lore-ledger.md") in out


def test_no_ledger_no_mention(repo: Path) -> None:
    pre = _run("pre-compact", "--cwd", str(repo), "--plain")
    start = _run("session-start", "--cwd", str(repo), "--plain", "--probe")
    assert "ledger" not in pre.lower()
    assert "ledger" not in start.lower()


def test_ledger_without_resume_line_gets_no_resume_offer(repo: Path) -> None:
    (repo / ".git" / "lore-ledger.md").write_text("- [adr] open — x\n")
    start = _run("session-start", "--cwd", str(repo), "--plain", "--probe")
    assert "resume" not in start.lower()


def test_malformed_ledger_never_crashes_the_hooks(repo: Path) -> None:
    (repo / ".git" / "lore-ledger.md").write_text("- [resume] yesterday — broken\n")
    _run("session-start", "--cwd", str(repo), "--plain", "--probe")
    out = _run("pre-compact", "--cwd", str(repo), "--plain")
    assert "lore-ledger.md" in out
