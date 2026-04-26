"""Task 8: orphan wikilink repair pass.

Highest-blast-radius pass in Plan 5 — can rewrite link syntax in note
bodies. Gated behind curator.defrag_curator.defrag_body_writes sub-flag
(default false). Default: proposes via body-proposals log, no mutation.
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


def _seed_vault(tmp_path: Path, *, defrag_body_writes: bool = False) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    wiki = lore_root / "wiki" / "w"
    (wiki / "sessions").mkdir(parents=True)
    # Write config enabling the sub-flag.
    cfg = (
        "curator:\n"
        "  curator_c:\n"
        "    enabled: true\n"
        f"    defrag_body_writes: {str(defrag_body_writes).lower()}\n"
    )
    (wiki / ".lore-wiki.yml").write_text(cfg)
    return lore_root


def _write_note(path: Path, body: str) -> Path:
    path.write_text(
        "---\n"
        "type: session\n"
        "created: 2026-04-21\n"
        "last_reviewed: 2026-04-21\n"
        "description: test\n"
        "tags: []\n"
        "---\n\n"
        f"{body}"
    )
    return path


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------


def test_find_orphan_links_detects_missing_targets(tmp_path: Path) -> None:
    from lore_curator.c_orphan_links import find_orphan_links

    lore_root = _seed_vault(tmp_path)
    wiki = lore_root / "wiki" / "w"
    _write_note(wiki / "sessions" / "a.md", "see [[missing-target]] and [[existing]]\n")
    _write_note(wiki / "sessions" / "existing.md", "present\n")

    orphans = find_orphan_links(wiki)
    assert len(orphans) == 1
    note, slug, _ = orphans[0]
    assert slug == "missing-target"


def test_find_orphan_links_detects_none_when_all_resolve(tmp_path: Path) -> None:
    from lore_curator.c_orphan_links import find_orphan_links

    lore_root = _seed_vault(tmp_path)
    wiki = lore_root / "wiki" / "w"
    _write_note(wiki / "sessions" / "a.md", "see [[b]]\n")
    _write_note(wiki / "sessions" / "b.md", "exists\n")

    assert find_orphan_links(wiki) == []


# ---------------------------------------------------------------------------
# Body-writes sub-flag OFF (default): no mutations, proposal log written
# ---------------------------------------------------------------------------


def test_orphan_sub_flag_off_no_body_mutation(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path, defrag_body_writes=False)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    a = _write_note(wiki / "sessions" / "a.md", "see [[foo-bar-typoo]]\n")
    _write_note(wiki / "sessions" / "foo-bar-typo.md", "present\n")

    before = a.read_bytes()
    client = FakeLlmClient({"is_rename": True, "confidence": 0.95})
    summary = orphan_links_pass(wiki, llm_client=client, dry_run=False)

    assert a.read_bytes() == before, "body must not mutate with sub-flag off"
    assert summary.get("orphan_flagged", 0) >= 1

    # Proposal log exists.
    from datetime import UTC, datetime
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log = lore_root / ".lore" / f"curator-c.body-proposals.{today}.log"
    assert log.exists()
    content = log.read_text()
    assert "foo-bar-typoo" in content
    assert "proposed" in content


# ---------------------------------------------------------------------------
# Body-writes sub-flag ON: actual rewrite happens
# ---------------------------------------------------------------------------


def test_orphan_sub_flag_on_rewrites_in_place(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path, defrag_body_writes=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    a = _write_note(wiki / "sessions" / "a.md", "see [[foo-bar-typoo]]\n")
    _write_note(wiki / "sessions" / "foo-bar-typo.md", "present\n")

    client = FakeLlmClient({"is_rename": True, "confidence": 0.95})
    summary = orphan_links_pass(wiki, llm_client=client, dry_run=False)

    text = a.read_text()
    assert "[[foo-bar-typoo]]" not in text, "old link must be rewritten"
    assert "[[foo-bar-typo]]" in text
    assert summary.get("orphan_rewritten", 0) >= 1


def test_orphan_preserves_display_text(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path, defrag_body_writes=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    a = _write_note(wiki / "sessions" / "a.md", "see [[foo-bar-typoo|the Thing]] for more\n")
    _write_note(wiki / "sessions" / "foo-bar-typo.md", "present\n")

    client = FakeLlmClient({"is_rename": True, "confidence": 0.95})
    orphan_links_pass(wiki, llm_client=client, dry_run=False)

    text = a.read_text()
    assert "[[foo-bar-typo|the Thing]]" in text


def test_orphan_preserves_crlf_and_trailing_newline(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path, defrag_body_writes=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"

    # Write with CRLF line endings.
    path = wiki / "sessions" / "a.md"
    crlf_body = (
        "---\r\n"
        "type: session\r\n"
        "created: 2026-04-21\r\n"
        "last_reviewed: 2026-04-21\r\n"
        "description: test\r\n"
        "tags: []\r\n"
        "---\r\n"
        "\r\n"
        "see [[foo-bar-typoo]] here\r\n"
    )
    path.write_bytes(crlf_body.encode("utf-8"))
    _write_note(wiki / "sessions" / "foo-bar-typo.md", "present\n")

    client = FakeLlmClient({"is_rename": True, "confidence": 0.95})
    orphan_links_pass(wiki, llm_client=client, dry_run=False)

    raw = path.read_bytes()
    # CRLF preserved, trailing newline preserved.
    assert b"\r\n" in raw
    assert raw.endswith(b"\r\n")
    assert b"[[foo-bar-typo]]" in raw


# ---------------------------------------------------------------------------
# Ambiguous candidates → no rewrite even with sub-flag on
# ---------------------------------------------------------------------------


def test_orphan_ambiguous_candidates_no_rewrite(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path, defrag_body_writes=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    a = _write_note(wiki / "sessions" / "a.md", "see [[zarr-chunking]]\n")
    # Two closely-matching candidates above the 0.7 threshold.
    _write_note(wiki / "sessions" / "zarr-chunking-one.md", "present\n")
    _write_note(wiki / "sessions" / "zarr-chunking-two.md", "present\n")

    before = a.read_bytes()
    client = FakeLlmClient({"is_rename": True, "confidence": 0.95})
    summary = orphan_links_pass(wiki, llm_client=client, dry_run=False)
    assert a.read_bytes() == before, "ambiguous → no rewrite"
    assert summary.get("orphan_ambiguous", 0) >= 1


def test_orphan_truly_deleted_is_flagged(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path, defrag_body_writes=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    _write_note(wiki / "sessions" / "a.md", "see [[completely-unrelated-xyz]]\n")
    _write_note(wiki / "sessions" / "something-else.md", "present\n")

    client = FakeLlmClient({"is_rename": True, "confidence": 0.95})
    summary = orphan_links_pass(wiki, llm_client=client, dry_run=False)
    assert summary.get("orphan_skipped_no_candidate", 0) >= 1


def test_orphan_skips_malformed(tmp_path: Path, monkeypatch) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path, defrag_body_writes=True)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    wiki = lore_root / "wiki" / "w"
    _write_note(wiki / "sessions" / "a.md", "see [[foo-bar-typoo]]\n")
    _write_note(wiki / "sessions" / "foo-bar-typo.md", "present\n")

    client = FakeLlmClient({"is_rename": True})  # missing confidence
    summary = orphan_links_pass(wiki, llm_client=client, dry_run=False)
    assert summary.get("orphan_skipped_malformed") == 1


def test_orphan_skipped_without_llm(tmp_path: Path) -> None:
    from lore_curator.c_orphan_links import orphan_links_pass

    lore_root = _seed_vault(tmp_path)
    wiki = lore_root / "wiki" / "w"
    summary = orphan_links_pass(wiki, llm_client=None, dry_run=False)
    assert summary == {"orphan_skipped_no_llm": 1}


def test_orphan_registered_in_defrag_passes() -> None:
    from lore_curator import c_orphan_links, defrag_curator  # noqa: F401
    assert c_orphan_links.orphan_links_pass in defrag_curator._DEFRAG_PASSES
