"""Task 10: high-tier degradation when models.high == "off"."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _seed_vault(tmp_path: Path, *, models_high: str = "claude-opus-4-7") -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    wiki = lore_root / "wiki" / "w"
    (wiki / "sessions").mkdir(parents=True)
    cfg = (
        "curator:\n"
        "  curator_c:\n"
        "    enabled: true\n"
        "models:\n"
        "  simple: claude-haiku-4-5\n"
        "  middle: claude-sonnet-4-6\n"
        f"  high: {models_high}\n"
    )
    (wiki / ".lore-wiki.yml").write_text(cfg)
    return lore_root


def test_resolve_tier_returns_high_when_enabled(tmp_path: Path) -> None:
    from lore_curator.c_passes import resolve_tier_for_pass

    lore_root = _seed_vault(tmp_path, models_high="claude-opus-4-7")
    wiki = lore_root / "wiki" / "w"
    model = resolve_tier_for_pass(
        wiki, pass_name="adjacent_merge", preferred_tier="high", lore_root=lore_root
    )
    assert model == "claude-opus-4-7"


def test_resolve_tier_degrades_to_middle_when_high_off(tmp_path: Path) -> None:
    from lore_curator.c_passes import resolve_tier_for_pass

    lore_root = _seed_vault(tmp_path, models_high="off")
    wiki = lore_root / "wiki" / "w"
    model = resolve_tier_for_pass(
        wiki, pass_name="adjacent_merge", preferred_tier="high", lore_root=lore_root
    )
    assert model == "claude-sonnet-4-6"


def test_high_off_emits_warning_event_every_call(tmp_path: Path) -> None:
    """Every resolution that degrades emits a warning — no sentinel, no
    once-per-run suppression.
    """
    from lore_curator.c_passes import resolve_tier_for_pass

    lore_root = _seed_vault(tmp_path, models_high="off")
    wiki = lore_root / "wiki" / "w"
    resolve_tier_for_pass(wiki, pass_name="adjacent_merge",
                         preferred_tier="high", lore_root=lore_root)
    resolve_tier_for_pass(wiki, pass_name="auto_supersede",
                         preferred_tier="high", lore_root=lore_root)

    events = lore_root / ".lore" / "hook-events.jsonl"
    assert events.exists()
    warnings = [
        json.loads(l) for l in events.read_text().splitlines() if l.strip()
    ]
    high_off = [
        e for e in warnings
        if e.get("event") == "curator-c" and e.get("outcome") == "high-tier-off"
    ]
    assert len(high_off) == 2, f"every degradation must emit; got {len(high_off)}"
    assert {e["error"]["pass"] for e in high_off} == {"adjacent_merge", "auto_supersede"}


def test_resolve_middle_tier_unchanged_when_high_off(tmp_path: Path) -> None:
    """Orphan + promotion passes use middle/simple and are unaffected by high:off."""
    from lore_curator.c_passes import resolve_tier_for_pass

    lore_root = _seed_vault(tmp_path, models_high="off")
    wiki = lore_root / "wiki" / "w"
    # Explicitly request middle — should always return middle, no warning.
    model = resolve_tier_for_pass(
        wiki, pass_name="orphan_links", preferred_tier="middle", lore_root=lore_root
    )
    assert model == "claude-sonnet-4-6"

    events = lore_root / ".lore" / "hook-events.jsonl"
    # Middle-tier requests never emit high-tier-off.
    if events.exists():
        warnings = [
            json.loads(l) for l in events.read_text().splitlines() if l.strip()
        ]
        high_off = [
            e for e in warnings
            if e.get("event") == "curator-c" and e.get("outcome") == "high-tier-off"
        ]
        assert high_off == [], "middle-tier requests must not emit high-tier-off"
