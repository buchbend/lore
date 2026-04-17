"""Tests for `lore_core.briefing` and the `lore briefing` CLI."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from lore_cli import briefing_cmd
from lore_core.briefing import gather, mark_incorporated


@pytest.fixture
def briefing_vault(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "ccat"
    (wiki / "sessions").mkdir(parents=True)

    def write_session(name: str, what: str, decisions: str = "") -> None:
        body = dedent(
            f"""\
            ---
            schema_version: 2
            type: session
            created: {name[:10]}
            last_reviewed: {name[:10]}
            description: "session {name}"
            ---

            ## What we worked on

            {what}

            ## Decisions made

            {decisions or "_None_"}
            """
        )
        (wiki / "sessions" / f"{name}.md").write_text(body)

    write_session("2026-04-15-fix-a", "- did the A thing")
    write_session("2026-04-16-fix-b", "- did the B thing", "- chose option Z because Y")
    write_session("2026-04-17-fix-c", "- did the C thing")

    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    return vault_root, wiki


def test_gather_returns_all_sessions_when_ledger_missing(briefing_vault):
    result = gather(wiki="ccat")
    assert "error" not in result
    assert result["wiki"] == "ccat"
    assert len(result["new_sessions"]) == 3
    assert result["ledger"]["last_briefing"] is None
    assert result["ledger"]["incorporated_count"] == 0


def test_gather_filters_by_ledger(briefing_vault):
    _, wiki = briefing_vault
    (wiki / ".briefing-ledger.json").write_text(
        json.dumps({"last_briefing": "2026-04-16", "incorporated": ["2026-04-15-fix-a.md"]})
    )
    result = gather(wiki="ccat")
    assert len(result["new_sessions"]) == 2
    slugs = [s["slug"] for s in result["new_sessions"]]
    assert "fix-a" not in slugs
    assert "fix-b" in slugs
    assert "fix-c" in slugs


def test_gather_filters_by_since_date(briefing_vault):
    result = gather(wiki="ccat", since="2026-04-17")
    assert len(result["new_sessions"]) == 1
    assert result["new_sessions"][0]["slug"] == "fix-c"


def test_gather_extracts_sections(briefing_vault):
    result = gather(wiki="ccat")
    s = next(s for s in result["new_sessions"] if s["slug"] == "fix-b")
    assert "what we worked on" in s["sections"]
    assert "B thing" in s["sections"]["what we worked on"]
    assert "decisions made" in s["sections"]
    assert "option Z" in s["sections"]["decisions made"]


def test_gather_no_sections_when_disabled(briefing_vault):
    result = gather(wiki="ccat", include_body_sections=False)
    s = result["new_sessions"][0]
    assert "sections" not in s


def test_gather_unknown_wiki(briefing_vault):
    result = gather(wiki="nonexistent")
    assert "error" in result


def test_mark_incorporated_writes_ledger(briefing_vault):
    _, wiki = briefing_vault
    result = mark_incorporated(
        wiki="ccat",
        session_paths=["2026-04-15-fix-a.md", "2026-04-16-fix-b.md"],
    )
    assert result["incorporated_count"] == 2
    assert result["last_briefing"] is not None
    ledger = json.loads((wiki / ".briefing-ledger.json").read_text())
    assert "2026-04-15-fix-a.md" in ledger["incorporated"]
    assert "2026-04-16-fix-b.md" in ledger["incorporated"]


def test_mark_incorporated_idempotent(briefing_vault):
    _, _ = briefing_vault
    mark_incorporated(wiki="ccat", session_paths=["2026-04-15-fix-a.md"])
    result = mark_incorporated(wiki="ccat", session_paths=["2026-04-15-fix-a.md"])
    # No new additions on second call
    assert result["added"] == []
    assert result["incorporated_count"] == 1


def test_cli_gather_emits_envelope(briefing_vault, capsys):
    rc = briefing_cmd.main(["gather", "--wiki", "ccat"])
    assert rc == 0
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["schema"] == "lore.briefing.gather/1"
    assert envelope["data"]["wiki"] == "ccat"


def test_cli_publish_markdown_sink(briefing_vault, tmp_path, capsys, monkeypatch):
    """Publish a briefing via the markdown sink to a target file."""
    out_path = tmp_path / "out.md"
    monkeypatch.setattr(
        "sys.stdin",
        type("S", (), {"read": staticmethod(lambda: "## Briefing\n\nbody\n")})(),
    )
    rc = briefing_cmd.main(
        ["publish", "--sink", "markdown", "--out", str(out_path), "--json"]
    )
    assert rc == 0
    assert out_path.exists()
    assert "Briefing" in out_path.read_text()


def test_cli_mark_writes_and_emits(briefing_vault, capsys):
    rc = briefing_cmd.main(
        ["mark", "--wiki", "ccat", "--session", "2026-04-15-fix-a.md"]
    )
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema"] == "lore.briefing.mark/1"
    assert envelope["data"]["incorporated_count"] == 1
