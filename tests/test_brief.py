"""`lore brief --json "<intent>"` — the orient homework in one call (PRD 0024)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from lore_cli.__main__ import app
from typer.testing import CliRunner


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    files = {
        "docs/adr/0001-ledger.md": (
            "# ADR 0001: Board ledger\n\nIntro.\n\n## Context\n\nThe board ledger holds"
            " the merges.\n\n## Decision\n\nKeep it.\n"
        ),
        "docs/adr/0002-other.md": "# ADR 0002: Search\n\n## Context\n\nFederated.\n",
        "CONTEXT.md": (
            "# Glossary\n\n- **Ledger** — the working list of one build run.\n\n"
            "- **Sink** — where a note lands.\n"
        ),
        "lib/board.py": "def parse_ledger():\n    return 1\n\n\ndef other():\n    return 2\n",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    # The code map reads tracked files only.
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    return repo


def _brief(repo: Path, intent: str) -> str:
    result = CliRunner().invoke(app, ["brief", "--json", intent, "--cwd", str(repo)])
    assert result.exit_code == 0, result.output
    return result.output


def test_brief_returns_pack_sections_codemap_and_glossary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))

    out = json.loads(_brief(repo, "update the board ledger"))
    assert out["pack"]["ranked"][0] == "docs/adr/0001-ledger.md"
    assert out["sections"]["docs/adr/0001-ledger.md"].startswith("## Context")
    assert "Decision" not in out["sections"]["docs/adr/0001-ledger.md"]
    assert any("parse_ledger" in line for line in out["codemap"])
    assert [g["term"] for g in out["glossary"]] == ["Ledger"]


def test_brief_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path / "no-such-vault"))

    assert _brief(repo, "board ledger") == _brief(repo, "board ledger")
