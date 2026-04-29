"""FTS5 search behaviour: AND-then-OR fallback, quoted-token safety, telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Vault with three notes — one matching both terms, two matching one each."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)

    (wiki / "concepts" / "curator-briefing.md").write_text(dedent("""\
        ---
        schema_version: 2
        type: concept
        description: "How the curator builds a briefing"
        tags: [curator, briefing]
        ---
        # Curator Briefing

        The curator drafts a briefing every morning.
        """))
    (wiki / "concepts" / "curator-only.md").write_text(dedent("""\
        ---
        schema_version: 2
        type: concept
        description: "About the curator"
        tags: [curator]
        ---
        # Curator

        Just the curator, no other terms.
        """))
    (wiki / "concepts" / "briefing-only.md").write_text(dedent("""\
        ---
        schema_version: 2
        type: concept
        description: "About briefings"
        tags: [briefing]
        ---
        # Briefing

        Just the briefing, no other terms.
        """))

    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    return vault_root


def _read_query_log(tmp_path: Path) -> list[dict]:
    log = tmp_path / "cache" / "query-log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def test_and_narrows_when_both_terms_co_occur(vault, tmp_path):
    """Multi-token query in AND mode returns only docs containing ALL tokens."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    hits = backend.search("curator briefing")
    paths = [h.path for h in hits]
    # The curator-briefing note has both terms; it must appear.
    assert any("curator-briefing" in p for p in paths)
    # AND mode should NOT include single-term-only notes.
    assert not any("curator-only" in p for p in paths)
    assert not any("briefing-only" in p for p in paths)

    # Telemetry: mode_final == "and" and and_hits > 0.
    log = _read_query_log(tmp_path)
    assert log, "expected at least one query-log record"
    rec = log[-1]
    assert rec["mode_final"] == "and"
    assert rec["and_hits"] >= 1


def test_or_fallback_when_no_doc_has_all_tokens(vault, tmp_path):
    """If AND yields zero, OR fallback returns docs matching any token."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    # No doc contains all three terms; AND returns 0; OR finds curator-only + curator-briefing.
    hits = backend.search("curator nonexistentterm")
    paths = [h.path for h in hits]
    assert any("curator" in p for p in paths), \
        f"expected curator notes via OR fallback, got {paths}"

    log = _read_query_log(tmp_path)
    rec = log[-1]
    assert rec["and_hits"] == 0
    assert rec["mode_final"] == "or"
    assert rec["or_hits"] >= 1


def test_single_token_query_works_in_both_modes(vault, tmp_path):
    """Single-token queries reduce to the same scan; AND mode succeeds."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    hits = backend.search("curator")
    assert hits

    log = _read_query_log(tmp_path)
    rec = log[-1]
    # Single token → AND query matches; OR fallback never runs.
    assert rec["mode_final"] == "and"
    assert rec["and_hits"] >= 1


def test_literal_fts_keyword_does_not_crash(vault, tmp_path):
    """User typing 'AND' / 'OR' / 'NOT' must not produce FTS5 syntax error."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    # Each of these would produce a bareword FTS5 operator without quoting.
    for q in ("AND", "OR", "NOT", "NEAR"):
        hits = backend.search(q)  # must not raise
        assert isinstance(hits, list)


def test_query_log_records_both_attempt_counts(vault, tmp_path):
    """When OR fallback fires, log captures BOTH and_hits and or_hits."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    backend.search("curator unrelatedmagicword")
    log = _read_query_log(tmp_path)
    rec = log[-1]
    assert "and_hits" in rec
    assert "or_hits" in rec
    assert rec["and_hits"] == 0
    assert rec["or_hits"] >= 1
    assert rec["sanitized_and"] != rec["sanitized_or"]


def test_empty_query_logs_empty_mode(vault, tmp_path):
    """Punctuation-only query yields no tokens; mode_final='empty'."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    hits = backend.search("???")
    assert hits == []

    log = _read_query_log(tmp_path)
    rec = log[-1]
    assert rec["mode_final"] == "empty"
    assert rec["and_hits"] == 0
