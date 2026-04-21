"""Task 7: auto-supersession proposal pass — marker only, never flips
`superseded_by` directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class _FakeBlock:
    def __init__(self, data: dict):
        self.type = "tool_use"
        self.input = data


class _FakeResp:
    def __init__(self, data: dict):
        self.content = [_FakeBlock(data)]


class FakeLlmClient:
    def __init__(self, response_data: dict):
        self._data = response_data
        class _M:
            def create(s, **kw):
                return _FakeResp(self._data)
        self.messages = _M()


def _write_decision(
    path: Path, *, title: str, created: str, tags: list[str], canonical: bool = False
) -> Path:
    canon_line = "canonical: true\n" if canonical else ""
    path.write_text(
        "---\n"
        "type: decision\n"
        f"title: {title}\n"
        f"tags: [{', '.join(tags)}]\n"
        f"created: {created}\n"
        f"last_reviewed: {created}\n"
        f"description: {title}\n"
        f"{canon_line}"
        "---\n\n"
        "body\n"
    )
    return path


def _seed(tmp_path: Path) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    wiki = lore_root / "wiki" / "w"
    (wiki / "sessions").mkdir(parents=True)
    return lore_root


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


def test_candidate_gen_requires_newer_created_date(tmp_path: Path) -> None:
    from lore_curator.c_auto_supersede import generate_supersede_candidates

    lore_root = _seed(tmp_path)
    wiki = lore_root / "wiki" / "w"
    _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"])
    _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["x"])

    pairs = generate_supersede_candidates(wiki)
    assert len(pairs) == 1
    older, newer = pairs[0]
    assert older.stem == "a"
    assert newer.stem == "b"


def test_candidate_gen_requires_overlapping_scope(tmp_path: Path) -> None:
    from lore_curator.c_auto_supersede import generate_supersede_candidates

    lore_root = _seed(tmp_path)
    wiki = lore_root / "wiki" / "w"
    _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"])
    _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["y"])

    assert generate_supersede_candidates(wiki) == []


def test_candidate_gen_ignores_non_decisions(tmp_path: Path) -> None:
    from lore_curator.c_auto_supersede import generate_supersede_candidates
    from tests.test_c_adjacent_merge import _write_note

    lore_root = _seed(tmp_path)
    wiki = lore_root / "wiki" / "w"
    _write_note(wiki / "sessions" / "s1.md", type_="session", title="S1", tags=["x"])
    _write_note(wiki / "sessions" / "s2.md", type_="session", title="S2", tags=["x"])

    assert generate_supersede_candidates(wiki) == []


# ---------------------------------------------------------------------------
# Proposal writes (markers only)
# ---------------------------------------------------------------------------


def test_supersede_proposes_markers_on_0_9_confidence(tmp_path, monkeypatch) -> None:
    from lore_curator.c_auto_supersede import auto_supersede_pass

    lore_root = _seed(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    older = _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"])
    newer = _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["x"])

    client = FakeLlmClient({"contradicts": True, "confidence": 0.9, "reason": "flipped"})
    summary = auto_supersede_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("auto_supersede_proposed") == 1

    older_text = older.read_text()
    newer_text = newer.read_text()
    assert "supersede_candidate" in older_text
    assert "[[b]]" in older_text
    assert "supersede_candidate_of" in newer_text
    assert "[[a]]" in newer_text
    # CRITICAL: never writes the actual superseded_by field.
    assert "superseded_by" not in older_text


def test_supersede_at_exact_0_85(tmp_path, monkeypatch) -> None:
    from lore_curator.c_auto_supersede import auto_supersede_pass

    lore_root = _seed(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"])
    _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["x"])

    client = FakeLlmClient({"contradicts": True, "confidence": 0.85, "reason": "boundary"})
    summary = auto_supersede_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("auto_supersede_proposed") == 1


def test_supersede_at_0_84_skips(tmp_path, monkeypatch) -> None:
    from lore_curator.c_auto_supersede import auto_supersede_pass

    lore_root = _seed(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"])
    _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["x"])

    client = FakeLlmClient({"contradicts": True, "confidence": 0.84, "reason": "just below"})
    summary = auto_supersede_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("auto_supersede_proposed", 0) == 0
    assert summary.get("auto_supersede_skipped_low_confidence") == 1


def test_supersede_respects_canonical_true(tmp_path, monkeypatch) -> None:
    from lore_curator.c_auto_supersede import auto_supersede_pass

    lore_root = _seed(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"], canonical=True)
    _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["x"])

    client = FakeLlmClient({"contradicts": True, "confidence": 0.99, "reason": "x"})
    summary = auto_supersede_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("auto_supersede_skipped_canonical") == 1
    assert summary.get("auto_supersede_proposed", 0) == 0


def test_supersede_idempotent(tmp_path, monkeypatch) -> None:
    from lore_curator.c_auto_supersede import auto_supersede_pass

    lore_root = _seed(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"])
    _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["x"])

    client = FakeLlmClient({"contradicts": True, "confidence": 0.9, "reason": "x"})
    auto_supersede_pass(wiki, anthropic_client=client, dry_run=False)
    first_text = (wiki / "sessions" / "a.md").read_text()
    auto_supersede_pass(wiki, anthropic_client=client, dry_run=False)
    second_text = (wiki / "sessions" / "a.md").read_text()

    # Marker should not be duplicated on second run.
    assert first_text == second_text


def test_supersede_skips_malformed(tmp_path, monkeypatch) -> None:
    from lore_curator.c_auto_supersede import auto_supersede_pass

    lore_root = _seed(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    _write_decision(wiki / "sessions" / "a.md", title="A", created="2026-04-20", tags=["x"])
    _write_decision(wiki / "sessions" / "b.md", title="B", created="2026-04-21", tags=["x"])

    client = FakeLlmClient({"contradicts": True})  # missing confidence + reason
    summary = auto_supersede_pass(wiki, anthropic_client=client, dry_run=False)
    assert summary.get("auto_supersede_skipped_malformed") == 1


def test_supersede_skipped_without_llm(tmp_path) -> None:
    from lore_curator.c_auto_supersede import auto_supersede_pass

    lore_root = _seed(tmp_path)
    wiki = lore_root / "wiki" / "w"
    summary = auto_supersede_pass(wiki, anthropic_client=None, dry_run=False)
    assert summary == {"auto_supersede_skipped_no_llm": 1}


def test_supersede_registered_in_defrag_passes() -> None:
    from lore_curator import c_auto_supersede, curator_c  # noqa: F401
    assert c_auto_supersede.auto_supersede_pass in curator_c._DEFRAG_PASSES
