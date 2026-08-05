"""Flag nudges — SessionStart pending count, directive, MCP tool.

The banner is the only push surface flags get, and it pushes a number:
ADR 0008 forbids showing flag content there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core import flag
from lore_core.session_start import (
    collect_session_facts,
    load_directive_lines,
    pending_flag_chip,
    render_session_banner,
)


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    (tmp_path / "wiki" / "lore" / "concepts").mkdir(parents=True)
    return tmp_path


SECRET_LEAD = "Zarquon protocol handshake is broken."


def _topic_note(vault: Path, slug: str = "reaper") -> Path:
    path = vault / "wiki" / "lore" / "concepts" / f"{slug}.md"
    path.write_text(
        "---\n"
        "schema_version: 2\n"
        "type: concept\n"
        "created: 2026-08-01\n"
        "last_reviewed: 2026-08-01\n"
        f"description: about {slug}\n"
        "tags: []\n"
        "---\n\n"
        f"# {slug}\n\nExisting prose.\n",
        encoding="utf-8",
    )
    return path


def _flag(vault: Path, lead: str) -> None:
    flag.write(
        lead,
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
        author="claude",
        now="2026-08-05",
    )


# ---------------------------------------------------------------------------
# AC8 — the banner shows a count, never content
# ---------------------------------------------------------------------------


def test_chip_is_empty_without_pending_flags(vault: Path):
    _topic_note(vault)
    assert pending_flag_chip(vault / "wiki" / "lore") == ""


def test_chip_counts_pending_flags(vault: Path):
    _topic_note(vault)
    _flag(vault, SECRET_LEAD)
    assert pending_flag_chip(vault / "wiki" / "lore") == "1 pending flag"
    _flag(vault, "Another one.")
    assert pending_flag_chip(vault / "wiki" / "lore") == "2 pending flags"


def test_banner_shows_the_count_and_no_flag_content(vault: Path):
    _topic_note(vault)
    _flag(vault, SECRET_LEAD)
    facts = collect_session_facts(vault / "wiki" / "lore", None)
    assert facts.flag_chip == "1 pending flag"
    banner = render_session_banner(facts)
    assert "1 pending flag" in banner
    assert SECRET_LEAD not in banner
    assert "Zarquon" not in banner


def test_banner_omits_the_chip_when_nothing_is_pending(vault: Path):
    _topic_note(vault)
    facts = collect_session_facts(vault / "wiki" / "lore", None)
    assert facts.flag_chip is None
    assert "pending flag" not in render_session_banner(facts)


def test_chip_survives_an_unreadable_wiki(tmp_path: Path):
    assert pending_flag_chip(tmp_path / "nope") == ""


# ---------------------------------------------------------------------------
# The directive tells an agent when to file a flag, and to self-check at end
# ---------------------------------------------------------------------------


def test_directive_carries_the_flag_rule_and_the_session_end_self_check():
    joined = "\n".join(load_directive_lines())
    assert "lore_flag" in joined
    assert "flag" in joined.lower()
    assert "session end" in joined.lower()


# ---------------------------------------------------------------------------
# MCP surface
# ---------------------------------------------------------------------------


def test_mcp_flag_writes_a_marked_block(vault: Path):
    _topic_note(vault)
    from lore_mcp.server import handle_flag

    out = handle_flag(
        lead="An agent files this.",
        body="Because it would be lost otherwise.",
        wiki="lore",
        target="concepts/reaper.md",
        refs=[{"type": "pr", "value": "357"}],
        transcript="tr-1",
        # A non-repo cwd keeps ref verification offline and deterministic:
        # nothing local to check, so `gh` is never reached.
        cwd=str(vault),
    )
    assert out["schema"] == "lore.flag.write/1"
    assert out["data"]["status"] == "written"
    text = (vault / "wiki/lore/concepts/reaper.md").read_text()
    assert "An agent files this." in text
    assert flag.UNREVIEWED_TOKEN in text


def test_mcp_flag_rejects_a_write_with_no_origin(vault: Path):
    _topic_note(vault)
    from lore_mcp.server import handle_flag

    out = handle_flag(lead="No origin.", wiki="lore", target="concepts/reaper.md")
    assert out["error"]["code"] == "missing_origin"
    assert flag.BLOCK_OPEN_PREFIX not in (
        vault / "wiki/lore/concepts/reaper.md"
    ).read_text()


def test_mcp_flag_rejects_an_empty_lead(vault: Path):
    _topic_note(vault)
    from lore_mcp.server import handle_flag

    out = handle_flag(lead="  ", wiki="lore", transcript="tr-1")
    assert out["error"]["code"] == "empty_flag"


def test_mcp_flag_reports_a_withheld_write(vault: Path):
    _topic_note(vault)
    from lore_mcp.server import handle_flag

    out = handle_flag(
        lead="Ping ops-oncall@example.com about it.",
        wiki="lore",
        target="concepts/reaper.md",
        transcript="tr-1",
    )
    assert out["data"]["status"] == "withheld"
    assert out["data"]["category"] == "email"


def test_mcp_tool_is_exposed_and_dispatchable():
    from lore_mcp.server import _tool_schema

    names = {t["name"] for t in _tool_schema()}
    assert "lore_flag" in names


# ---------------------------------------------------------------------------
# The chip and the last-active-day recap share the banner
# ---------------------------------------------------------------------------


def test_chip_and_recap_coexist(vault: Path):
    """Neither nudge suppresses the other on a session where both apply."""
    from datetime import UTC, datetime

    from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry

    _topic_note(vault)
    _flag(vault, SECRET_LEAD)
    TranscriptLedger(vault).upsert(
        TranscriptLedgerEntry(
            integration="claude-code",
            transcript_id="tr-1",
            path=vault / "tr-1.jsonl",
            directory=vault / "proj",
            digested_hash=None,
            digested_index_hint=None,
            synthesised_hash=None,
            last_mtime=datetime(2026, 8, 4, 9, 0, tzinfo=UTC),
            curator_a_run=None,
            noteworthy=None,
            session_note=None,
            linkage={"repo": "buchbend/lore", "branch": "feat/357-flag", "prs": [365]},
        )
    )

    facts = collect_session_facts(vault / "wiki" / "lore", None)
    assert facts.flag_chip == "1 pending flag"
    assert facts.recap

    banner = render_session_banner(facts)
    assert "1 pending flag" in banner
    assert "buchbend/lore" in banner
    assert SECRET_LEAD not in banner
