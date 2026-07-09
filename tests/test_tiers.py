"""Model-tier resolution: table lookup, host detection, config overrides.

Ports the semantics of MODEL-TIERS.md from ccat-agent-workflow: four
ordinal tiers (frontier > strong > mid > cheap), one column per host,
unknown tier/host fails loudly, user config overrides win.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lore_core.tiers import TABLE, TIER_ORDER, TierResolutionError, resolve_tier


def _mk_root(tmp_path: Path) -> Path:
    (tmp_path / ".lore").mkdir()
    return tmp_path


def test_tier_order_is_the_four_semantic_tiers():
    assert TIER_ORDER == ("frontier", "strong", "mid", "cheap")


def test_table_has_an_entry_for_every_tier_on_every_host():
    for host, tiers in TABLE.items():
        for tier in TIER_ORDER:
            assert tier in tiers, f"host {host!r} missing tier {tier!r}"


def test_resolve_known_tier_on_claude(tmp_path):
    root = _mk_root(tmp_path)
    assert resolve_tier("mid", host="claude", lore_root=root) == TABLE["claude"]["mid"]


def test_resolve_unknown_tier_fails_loudly(tmp_path):
    root = _mk_root(tmp_path)
    with pytest.raises(TierResolutionError, match="unknown tier"):
        resolve_tier("premium", host="claude", lore_root=root)


def test_resolve_unknown_host_fails_loudly(tmp_path):
    root = _mk_root(tmp_path)
    with pytest.raises(TierResolutionError, match="unknown host"):
        resolve_tier("mid", host="nonexistent-ide", lore_root=root)


def test_config_override_wins_over_table_default(tmp_path):
    root = _mk_root(tmp_path)
    (root / ".lore" / "config.yml").write_text(
        "tiers:\n  overrides:\n    claude:\n      frontier: claude-opus-4-9\n"
    )
    assert resolve_tier("frontier", host="claude", lore_root=root) == "claude-opus-4-9"
    # untouched tier still falls through to the shipped table
    assert resolve_tier("mid", host="claude", lore_root=root) == TABLE["claude"]["mid"]


def test_detect_host_recognizes_claude_env(monkeypatch):
    from lore_core.tiers import detect_host

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    assert detect_host() == "claude"


def test_detect_host_recognizes_cursor_env(monkeypatch):
    from lore_core.tiers import detect_host

    for var in ("CLAUDECODE", "CLAUDE_PROJECT_DIR", "CLAUDE_SESSION_ID", "CLAUDE_CODE_EXECPATH"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CURSOR_PROJECT_DIR", "/some/project")
    assert detect_host() == "cursor"


def test_detect_host_unknown_fails_loudly(monkeypatch):
    from lore_core.tiers import detect_host

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    with pytest.raises(TierResolutionError, match="unable to detect"):
        detect_host()
