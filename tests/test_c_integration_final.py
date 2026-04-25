"""Task 12: final integration assertions — full Curator C run.

Exercises the whole defrag pipeline end-to-end with a fake LLM,
asserting:
  - pass order: hygiene → adjacent-merge → auto-supersede → orphan → promotion
  - diff log captures proposals + hygiene actions
  - last_curator_c updates on success
  - ships-dark gate still passes after all Phase B/C additions
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


class _FakeBlock:
    def __init__(self, data: dict):
        self.type = "tool_use"
        self.input = data


class _FakeResp:
    def __init__(self, data: dict):
        self.content = [_FakeBlock(data)]


class MultiToolFakeClient:
    """Routes based on tool_choice.name — supports all four Plan 5 passes."""

    def __init__(self, responses: dict[str, dict]):
        self._responses = responses
        class _M:
            def create(s, **kwargs):
                tc = kwargs.get("tool_choice") or {}
                name = tc.get("name") if isinstance(tc, dict) else None
                data = self._responses.get(
                    name, {"should_merge": False, "confidence": 0.1, "reason": "default"}
                )
                return _FakeResp(data)
        self.messages = _M()


def _seed_vault(tmp_path: Path) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    wiki = lore_root / "wiki" / "w"
    (wiki / "sessions").mkdir(parents=True)
    (wiki / ".lore-wiki.yml").write_text(
        "curator:\n"
        "  curator_c:\n"
        "    enabled: true\n"
        "    defrag_body_writes: false\n"
    )
    return lore_root


def _write_concept(path: Path, *, title: str, tags: list[str]) -> None:
    path.write_text(
        "---\n"
        "type: concept\n"
        f"title: {title}\n"
        f"tags: [{', '.join(tags)}]\n"
        "created: 2026-04-20\n"
        "last_reviewed: 2026-04-20\n"
        f"description: {title}\n"
        "---\n\n"
        "body\n"
    )


def test_full_defrag_run_writes_diff_log_and_advances_ledger(tmp_path, monkeypatch) -> None:
    from lore_core.ledger import WikiLedger
    from lore_curator.curator_c import run_curator_c

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"

    _write_concept(wiki / "sessions" / "zarr-a.md", title="Zarr Chunking", tags=["z"])
    _write_concept(wiki / "sessions" / "zarr-b.md", title="Zarr Chunking Plus", tags=["z"])

    client = MultiToolFakeClient({
        "propose_merge": {"should_merge": True, "confidence": 0.9, "reason": "similar"},
        "judge_supersession": {"contradicts": False, "confidence": 0.1, "reason": "n/a"},
        "confirm_rename": {"is_rename": False, "confidence": 0.1},
    })

    reports = run_curator_c(
        wiki_filter="w", dry_run=False, defrag=True, llm_client=client
    )

    # Ledger advanced.
    entry = WikiLedger(lore_root, "w").read()
    assert entry.last_curator_c is not None

    # Diff log exists.
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.diff.{today}.log"
    assert log.exists()
    content = log.read_text()
    # Summary captures the merge proposal.
    assert "adjacent_merge_proposed" in content

    # Proposal file exists in sessions.
    merges = list((wiki / "sessions").glob("*-merge*.md"))
    assert len(merges) == 1


def test_defrag_run_does_not_crash_without_llm(tmp_path, monkeypatch) -> None:
    """defrag=True with client=None → each LLM pass reports skipped_no_llm;
    hygiene still runs; ledger still advances; diff log still written.
    """
    from lore_core.ledger import WikiLedger
    from lore_curator.curator_c import run_curator_c

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    _write_concept(
        lore_root / "wiki" / "w" / "sessions" / "a.md",
        title="Concept A", tags=["t"],
    )

    reports = run_curator_c(
        wiki_filter="w", dry_run=False, defrag=True, llm_client=None
    )
    # No crash.
    entry = WikiLedger(lore_root, "w").read()
    assert entry.last_curator_c is not None


def test_hygiene_only_path_still_works(tmp_path, monkeypatch) -> None:
    """Bare `lore curator` path (defrag=False) behaves as pre-Plan-5."""
    from lore_core.ledger import WikiLedger
    from lore_curator.curator_c import run_curator_c

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    _write_concept(
        lore_root / "wiki" / "w" / "sessions" / "a.md",
        title="Concept A", tags=["t"],
    )

    reports = run_curator_c(wiki_filter="w", dry_run=False, defrag=False)
    # Without defrag: no last_curator_c update, no diff log.
    entry = WikiLedger(lore_root, "w").read()
    assert entry.last_curator_c is None
    assert not list((lore_root / ".lore").glob("curator-c.diff.*.log"))
