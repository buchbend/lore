"""Task 6: adjacent-concept merge proposal pass."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


class _FakeBlock:
    def __init__(self, data: dict):
        self.type = "tool_use"
        self.input = data


class _FakeResp:
    def __init__(self, data: dict):
        self.content = [_FakeBlock(data)]


class FakeLlmClient:
    """Minimal LlmClient returning a fixed tool-use block."""

    def __init__(self, response_data: dict):
        self._data = response_data

        class _Messages:
            def create(s, **kwargs):
                return _FakeResp(self._data)

        self.messages = _Messages()


def _write_note(path: Path, *, type_: str, title: str, tags: list[str], body: str = "body") -> Path:
    path.write_text(
        "---\n"
        f"type: {type_}\n"
        f"title: {title}\n"
        f"tags: [{', '.join(tags)}]\n"
        "created: 2026-04-21\n"
        "last_reviewed: 2026-04-21\n"
        f"description: {title}\n"
        "---\n\n"
        f"{body}\n"
    )
    return path


def _seed_vault(tmp_path: Path) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    wiki = lore_root / "wiki" / "testwiki"
    (wiki / "sessions").mkdir(parents=True)
    return lore_root


# ---------------------------------------------------------------------------
# Candidate generation (exercised independently)
# ---------------------------------------------------------------------------


def test_generate_merge_candidates_filters_low_overlap(tmp_path: Path) -> None:
    """Two notes without shared tags or fuzzy-title overlap → no candidate pair."""
    from lore_curator.c_adjacent_merge import generate_merge_candidates

    lore_root = _seed_vault(tmp_path)
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "a.md", type_="concept", title="Zarr Chunking", tags=["storage"])
    _write_note(wiki / "sessions" / "b.md", type_="concept", title="MCP Adapter", tags=["adapters"])

    pairs = generate_merge_candidates(wiki)
    assert pairs == [], "no shared tags → no candidate pair"


def test_generate_merge_candidates_pairs_shared_tag_fuzzy_title(tmp_path: Path) -> None:
    from lore_curator.c_adjacent_merge import generate_merge_candidates

    lore_root = _seed_vault(tmp_path)
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "zarr-chunking.md", type_="concept", title="Zarr Chunking", tags=["storage", "format"])
    _write_note(wiki / "sessions" / "zarr-chunking-strategy.md", type_="concept", title="Zarr Chunking Strategy", tags=["storage"])

    pairs = generate_merge_candidates(wiki)
    assert len(pairs) == 1


def test_candidates_skip_session_notes(tmp_path: Path) -> None:
    from lore_curator.c_adjacent_merge import generate_merge_candidates

    lore_root = _seed_vault(tmp_path)
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "s1.md", type_="session", title="Session One", tags=["z"])
    _write_note(wiki / "sessions" / "s2.md", type_="session", title="Session Two", tags=["z"])

    pairs = generate_merge_candidates(wiki)
    assert pairs == [], "type=session never candidates for merge"


# ---------------------------------------------------------------------------
# Pass behaviour (with fake LLM)
# ---------------------------------------------------------------------------


def test_merge_proposes_new_note_on_0_9_confidence(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "zarr-chunking.md", type_="concept", title="Zarr Chunking", tags=["storage"])
    _write_note(wiki / "sessions" / "zarr-chunking-strategy.md", type_="concept", title="Zarr Chunking Strategy", tags=["storage"])

    client = FakeLlmClient(
        {"should_merge": True, "confidence": 0.9, "reason": "same concept",
         "merged_title": "Zarr Chunking", "merged_description": "merged"}
    )

    summary = adjacent_merge_pass(wiki, anthropic_client=client, dry_run=False)

    assert summary.get("adjacent_merge_proposed") == 1
    proposals = list((wiki / "sessions").glob("*-merge*.md"))
    assert len(proposals) == 1
    draft = proposals[0].read_text()
    assert "draft: true" in draft
    assert "merge_candidate_sources" in draft


def test_merge_at_exact_0_8_is_included(tmp_path: Path, monkeypatch) -> None:
    """Boundary: confidence == 0.8 → included (>= inclusive)."""
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "a.md", type_="concept", title="Cache", tags=["t"])
    _write_note(wiki / "sessions" / "ab.md", type_="concept", title="Cached", tags=["t"])

    client = FakeLlmClient(
        {"should_merge": True, "confidence": 0.8, "reason": "at threshold"}
    )
    summary = adjacent_merge_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("adjacent_merge_proposed") == 1


def test_merge_at_0_79_skips(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "a.md", type_="concept", title="Cache", tags=["t"])
    _write_note(wiki / "sessions" / "ab.md", type_="concept", title="Cached", tags=["t"])

    client = FakeLlmClient(
        {"should_merge": True, "confidence": 0.79, "reason": "just below"}
    )
    summary = adjacent_merge_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("adjacent_merge_proposed", 0) == 0
    assert summary.get("adjacent_merge_skipped_low_confidence") == 1


def test_merge_skips_malformed_llm_response(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "a.md", type_="concept", title="Cache", tags=["t"])
    _write_note(wiki / "sessions" / "ab.md", type_="concept", title="Cached", tags=["t"])

    # Missing `reason` field.
    client = FakeLlmClient({"should_merge": True, "confidence": 0.9})
    summary = adjacent_merge_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("adjacent_merge_skipped_malformed") == 1
    assert not list((wiki / "sessions").glob("*-merge*.md"))


def test_merge_never_edits_originals(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "testwiki"
    a = _write_note(wiki / "sessions" / "a.md", type_="concept", title="Cache", tags=["t"])
    b = _write_note(wiki / "sessions" / "ab.md", type_="concept", title="Cached", tags=["t"])
    before_a = a.read_bytes()
    before_b = b.read_bytes()

    client = FakeLlmClient({"should_merge": True, "confidence": 0.9, "reason": "x"})
    adjacent_merge_pass(wiki, anthropic_client=client, dry_run=False)

    assert a.read_bytes() == before_a
    assert b.read_bytes() == before_b


def test_merge_idempotent_same_sources(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "a.md", type_="concept", title="Cache", tags=["t"])
    _write_note(wiki / "sessions" / "ab.md", type_="concept", title="Cached", tags=["t"])

    client = FakeLlmClient({"should_merge": True, "confidence": 0.9, "reason": "x"})
    adjacent_merge_pass(wiki, anthropic_client=client, dry_run=False)
    first_drafts = sorted((wiki / "sessions").glob("*-merge*.md"))
    adjacent_merge_pass(wiki, anthropic_client=client, dry_run=False)
    second_drafts = sorted((wiki / "sessions").glob("*-merge*.md"))

    assert first_drafts == second_drafts, "second run must not create duplicate drafts"


def test_merge_dry_run_writes_no_notes(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "a.md", type_="concept", title="Cache", tags=["t"])
    _write_note(wiki / "sessions" / "ab.md", type_="concept", title="Cached", tags=["t"])

    client = FakeLlmClient({"should_merge": True, "confidence": 0.9, "reason": "x"})
    summary = adjacent_merge_pass(wiki, anthropic_client=client, dry_run=True)
    assert summary.get("adjacent_merge_proposed") == 1  # counted
    assert not list((wiki / "sessions").glob("*-merge*.md")), "dry-run writes no file"


def test_merge_skipped_without_llm(tmp_path: Path) -> None:
    from lore_curator.c_adjacent_merge import adjacent_merge_pass

    lore_root = _seed_vault(tmp_path)
    wiki = lore_root / "wiki" / "testwiki"
    _write_note(wiki / "sessions" / "a.md", type_="concept", title="Cache", tags=["t"])
    _write_note(wiki / "sessions" / "ab.md", type_="concept", title="Cached", tags=["t"])

    summary = adjacent_merge_pass(wiki, anthropic_client=None, dry_run=False)
    assert summary == {"adjacent_merge_skipped_no_llm": 1}


def test_merge_pass_registered_in_defrag_passes() -> None:
    """Confirms the pass is picked up by the integration skeleton."""
    from lore_curator import c_adjacent_merge, curator_c  # noqa: F401
    assert c_adjacent_merge.adjacent_merge_pass in curator_c._DEFRAG_PASSES
