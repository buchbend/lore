"""FTS5 index splits body into reload_safe + human_only.

LLM-facing search (``redact_human_only=True``) returns notes only when
the matching term appears in title / description / tags / reload-safe
body. A term that lives *only* in the human-only region must NOT
surface to the LLM — option (b) clean exclusion (no snippet preview,
no ranked hit).

Human-facing search (``redact_human_only=False``, the default) matches
across the whole note including the human-only region.

Backwards compatibility: a note without the marker indexes its entire
body into ``body_reload_safe`` — old vaults work unchanged.
"""

from __future__ import annotations

from textwrap import dedent

import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Vault with three notes exercising the two-region contract.

    * ``human-only-secret``: matching term ONLY in human-only region.
    * ``reload-safe-public``: matching term ONLY in reload-safe region.
    * ``legacy-no-marker``: matching term in body, no marker — the
      whole body is reload-safe.
    """
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "sessions").mkdir(parents=True)

    (wiki / "sessions" / "human-only-secret.md").write_text(dedent("""\
        ---
        schema_version: 2
        type: session
        description: "Session note with humanonly region"
        tags: [session]
        ---
        # Session A

        Public summary of work.

        <!-- lore:human-only -->

        Tentative thinking: we leaned toward the magicwordhuman approach,
        but it might be wrong. This region is gated.
        """))

    (wiki / "sessions" / "reload-safe-public.md").write_text(dedent("""\
        ---
        schema_version: 2
        type: session
        description: "Session note with reloadsafe content only"
        tags: [session]
        ---
        # Session B

        The magicwordhuman appears here in the reload-safe region,
        not gated.

        <!-- lore:human-only -->

        Some private thought unrelated to the search term.
        """))

    (wiki / "sessions" / "legacy-no-marker.md").write_text(dedent("""\
        ---
        schema_version: 2
        type: session
        description: "Legacy session with no marker"
        tags: [session]
        ---
        # Legacy Session

        The magicwordhuman appears here in a pre-two-region note.
        Whole body is treated as reload-safe.
        """))

    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    return vault_root


def test_llm_facing_search_excludes_human_only_match(vault):
    """A term that lives only in human-only must NOT surface to LLM-facing search."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    hits = backend.search("magicwordhuman", redact_human_only=True)
    paths = [h.path for h in hits]

    # The note with the term only in human-only must not appear.
    assert not any("human-only-secret" in p for p in paths), (
        f"human-only-only match leaked into LLM-facing search: {paths}"
    )
    # The reload-safe note and the legacy-no-marker note DO appear.
    assert any("reload-safe-public" in p for p in paths), paths
    assert any("legacy-no-marker" in p for p in paths), paths


def test_human_facing_search_returns_human_only_match(vault):
    """Default search (human-facing) matches the whole note including human-only."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    hits = backend.search("magicwordhuman")  # default redact_human_only=False
    paths = [h.path for h in hits]

    # All three notes contain the term somewhere — all three must surface.
    assert any("human-only-secret" in p for p in paths), paths
    assert any("reload-safe-public" in p for p in paths), paths
    assert any("legacy-no-marker" in p for p in paths), paths


def test_legacy_note_without_marker_indexes_full_body_as_reload_safe(vault):
    """Old notes (no marker) are entirely reload-safe — backwards compat."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    backend.reindex()

    # LLM-facing must find the legacy note even though it predates the marker.
    hits = backend.search("magicwordhuman", redact_human_only=True)
    paths = [h.path for h in hits]
    assert any("legacy-no-marker" in p for p in paths), (
        f"legacy note without marker must index its full body into reload_safe: {paths}"
    )


def test_llm_facing_match_against_title_still_works(tmp_path, monkeypatch):
    """Redact filter does not break matches against title / description / tags."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "unique-title-token.md").write_text(dedent("""\
        ---
        schema_version: 2
        type: concept
        description: "Concept whose match-token is in the title"
        tags: [demo]
        ---
        # Title-zzzunique-marker

        Body content unrelated.

        <!-- lore:human-only -->

        Notes I keep private.
        """))
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))

    from lore_search.fts import FtsBackend
    backend = FtsBackend()
    backend.reindex()

    hits = backend.search("zzzunique-marker", redact_human_only=True)
    assert any("unique-title-token" in h.path for h in hits), hits


def test_schema_has_separate_body_columns(vault):
    """notes_fts virtual table declares body_reload_safe + body_human_only."""
    import sqlite3
    from lore_search.fts import FtsBackend, _db_path

    FtsBackend().reindex()
    conn = sqlite3.connect(_db_path())
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='notes_fts'"
        ).fetchone()
    finally:
        conn.close()
    sql = row[0] if row else ""
    assert "body_reload_safe" in sql, f"missing body_reload_safe column: {sql!r}"
    assert "body_human_only" in sql, f"missing body_human_only column: {sql!r}"
