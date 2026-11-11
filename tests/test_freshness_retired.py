"""The freshness layer and the verdict worklist are retired (issue #591)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

REPO = Path(__file__).resolve().parent.parent
NOTE = "---\ntype: concept\nstatus: stale\ndescription: zebra note\n---\nzebra body [[missing]]\n"


def _wiki(tmp_path: Path, monkeypatch) -> Path:
    wiki = tmp_path / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "note.md").write_text(NOTE)
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    return wiki


def test_read_reply_holds_no_freshness_key(tmp_path, monkeypatch):
    from lore_mcp.server import handle_read

    _wiki(tmp_path, monkeypatch)
    assert "freshness" not in handle_read("concepts/note.md", wiki="demo")
    assert "freshness" not in handle_read("concepts/note.md", wiki="demo", section="x")


def test_search_hit_holds_no_freshness_key_and_starts_no_git(tmp_path, monkeypatch):
    from lore_mcp.server import _wiki_hits

    _wiki(tmp_path, monkeypatch)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    _wiki_hits("zebra", wiki="demo")  # first call may reindex

    def no_git(cmd, *a, **kw):
        raise AssertionError(f"lore_search started a process: {cmd}")

    monkeypatch.setattr(subprocess, "run", no_git)
    monkeypatch.setattr(subprocess, "Popen", no_git)
    hits = _wiki_hits("zebra", wiki="demo")
    assert hits and all("freshness" not in h for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_verdict_command_is_unknown():
    from lore_cli.__main__ import app

    result = CliRunner().invoke(app, ["verdict", "pending"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_plugin_ships_no_verify_skill():
    assert not (REPO / "skills" / "verify").exists()


def test_banner_has_no_pending_verdict_chip(tmp_path, monkeypatch):
    from lore_core.session_start import collect_session_facts, render_session_banner

    wiki = _wiki(tmp_path, monkeypatch)
    (wiki / "_catalog.json").write_text(
        json.dumps({"orphan_set": ["concepts/note.md"], "notes": []})
    )
    facts = collect_session_facts(wiki, None)
    assert not hasattr(facts, "pending_chip")
    banner = render_session_banner(facts)
    assert "verdict" not in banner


def test_lint_writes_no_orphan_set(tmp_path, monkeypatch):
    from lore_core.lint import _regenerate_wiki

    wiki = _wiki(tmp_path, monkeypatch)
    _regenerate_wiki(wiki, [], [])
    assert "orphan_set" not in json.loads((wiki / "_catalog.json").read_text())
    assert not (wiki / "_verdicts").exists()
