"""Tests for ``lore_core.journal`` — AI + human freeform side-chains."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lore_core import journal


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    monkeypatch.delenv("LORE_AI_AUTHOR", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL_ID", raising=False)
    monkeypatch.delenv("LORE_USER_HANDLE", raising=False)
    return tmp_path


def test_journal_path_resolves_under_vault_top_level(vault):
    assert journal.journal_path("ai") == vault / "journals" / "ai.md"
    assert journal.journal_path("human") == vault / "journals" / "human.md"


def test_invalid_kind_raises(vault):
    with pytest.raises(ValueError):
        journal.journal_path("nonsense")  # type: ignore[arg-type]


def test_disabled_by_default(vault):
    assert journal.enabled() is False


def test_set_enabled_persists_flag(vault):
    journal.set_enabled(True)
    assert journal.enabled() is True
    journal.set_enabled(False)
    assert journal.enabled() is False


def test_set_enabled_preserves_other_keys(vault):
    cfg = vault / ".lore" / "config.yml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(yaml.safe_dump({"curator": {"backend": "auto"}}))
    journal.set_enabled(True)
    raw = yaml.safe_load(cfg.read_text())
    assert raw["curator"]["backend"] == "auto"
    assert raw["journal"]["enabled"] is True


def test_write_creates_file_with_header(vault):
    result = journal.write(
        "ai", "first thought", author="opus", timestamp="2026-04-28T12:00"
    )
    path = Path(result["path"])
    text = path.read_text()
    assert text.startswith("# AI Journal")
    assert "## 2026-04-28T12:00 — opus" in text
    assert "first thought" in text


def test_write_prepends_newest_first(vault):
    journal.write("human", "older", author="me", timestamp="2026-04-28T10:00")
    journal.write("human", "newer", author="me", timestamp="2026-04-28T11:00")
    text = journal.journal_path("human").read_text()
    assert text.index("newer") < text.index("older")


def test_read_returns_entries_newest_first(vault):
    journal.write("ai", "alpha", author="claude", timestamp="2026-04-28T09:00")
    journal.write("ai", "beta", author="claude", timestamp="2026-04-28T10:00")
    entries = journal.read("ai")
    assert [e["body"] for e in entries] == ["beta", "alpha"]
    assert entries[0]["timestamp"] == "2026-04-28T10:00"


def test_read_limit(vault):
    for i in range(5):
        journal.write("ai", f"e{i}", author="claude", timestamp=f"2026-04-28T0{i}:00")
    entries = journal.read("ai", limit=2)
    assert len(entries) == 2
    assert entries[0]["body"] == "e4"


def test_write_rejects_empty(vault):
    with pytest.raises(ValueError):
        journal.write("ai", "   ", author="claude")


def test_default_author_ai_uses_env_then_slug(vault, monkeypatch):
    monkeypatch.setenv("LORE_AI_AUTHOR", "Claude Opus 4.7")
    assert journal.default_author("ai") == "claude-opus-47"


def test_default_author_ai_fallback(vault):
    assert journal.default_author("ai") == "claude"


def test_default_author_human_uses_handle_env(vault, monkeypatch):
    monkeypatch.setenv("LORE_USER_HANDLE", "buchbend")
    assert journal.default_author("human") == "buchbend"


def test_read_missing_file_returns_empty(vault):
    assert journal.read("ai") == []


def test_write_under_existing_unheadered_file_does_not_clobber(vault):
    p = journal.journal_path("human")
    p.parent.mkdir(parents=True)
    p.write_text("# Custom Header\n\nold content here\n\n", encoding="utf-8")
    journal.write("human", "new note", author="me", timestamp="2026-04-28T12:00")
    text = p.read_text()
    assert "old content here" in text
    assert "new note" in text
    assert text.index("new note") < text.index("old content here")


def test_multiline_entry_body_preserved(vault):
    journal.write(
        "ai",
        "line one\nline two\n\nline four",
        author="claude",
        timestamp="2026-04-28T12:00",
    )
    entries = journal.read("ai")
    assert entries[0]["body"] == "line one\nline two\n\nline four"


def test_root_config_overrides_via_env(tmp_path, monkeypatch):
    """Confirm get_lore_root → load_root_config → journal.enabled wiring."""
    monkeypatch.setenv("LORE_ROOT", str(tmp_path))
    journal.set_enabled(True)
    # New process-style import path: no cached config.
    from lore_core.root_config import load_root_config
    cfg = load_root_config(tmp_path)
    assert cfg.journal.enabled is True


# ---------------------------------------------------------------------------
# SessionStart directive injection
# ---------------------------------------------------------------------------


def test_session_start_directive_off_by_default(vault):
    from lore_cli.hooks import _journal_directive_lines

    assert _journal_directive_lines() == []


def test_session_start_directive_on_when_enabled(vault):
    from lore_cli.hooks import _journal_directive_lines

    journal.set_enabled(True)
    lines = _journal_directive_lines()
    assert lines, "expected directive lines when flag is on"
    joined = "\n".join(lines)
    assert "AI Journal active" in joined
    assert "lore_journal_write" in joined


# ---------------------------------------------------------------------------
# MCP handler smoke tests
# ---------------------------------------------------------------------------


def test_mcp_journal_write_round_trip(vault):
    from lore_mcp.server import handle_journal_read, handle_journal_write

    out = handle_journal_write(
        kind="ai", text="hello from MCP", author="opus"
    )
    assert out["schema"] == "lore.journal.write/1"
    assert "error" not in out

    read_out = handle_journal_read(kind="ai", limit=5)
    assert read_out["schema"] == "lore.journal.read/1"
    entries = read_out["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["body"] == "hello from MCP"
    assert entries[0]["author"] == "opus"


def test_mcp_journal_write_rejects_invalid_kind(vault):
    from lore_mcp.server import handle_journal_write

    out = handle_journal_write(kind="bogus", text="x")
    assert "error" in out
    assert out["error"]["code"] == "invalid_kind"


def test_mcp_journal_write_rejects_empty(vault):
    from lore_mcp.server import handle_journal_write

    out = handle_journal_write(kind="ai", text="   ")
    assert "error" in out
    assert out["error"]["code"] == "empty_entry"
