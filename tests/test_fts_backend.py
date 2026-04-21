"""FTS5 backend: schema sanity + automatic recovery from the legacy
stock-contentless schema that cannot handle DELETE.

The original schema declared the FTS5 virtual table with `content=''`
but without `contentless_delete=1`. In that mode SQLite refuses
`DELETE FROM notes_fts WHERE rowid=?`, which `_upsert` and the
end-of-reindex sweep both depend on. Users who indexed with the old
lore then upgrade to one that reindexes on every resume/search hit
`OperationalError: cannot DELETE from contentless fts5 table: notes_fts`
and are stuck.

These tests pin the fix: the new schema declares `contentless_delete=1`,
and connect-time migration drops any pre-existing legacy index so the
next reindex rebuilds cleanly — no manual intervention.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Minimal vault + isolated search cache."""
    vault_root = tmp_path / "vault"
    wiki = vault_root / "wiki" / "demo"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "concepts" / "hsm-ceremony.md").write_text(
        dedent(
            """\
            ---
            schema_version: 2
            type: concept
            description: "HSM key ceremony and stepca bootstrap"
            tags: [hsm, stepca, pki]
            ---
            # HSM Ceremony

            Provisioning the hardware security module via stepca.
            """
        )
    )
    (wiki / "concepts" / "unrelated.md").write_text(
        dedent(
            """\
            ---
            schema_version: 2
            type: concept
            description: "another note"
            tags: [misc]
            ---
            # Other
            """
        )
    )
    monkeypatch.setenv("LORE_ROOT", str(vault_root))
    monkeypatch.setenv("LORE_CACHE", str(tmp_path / "cache"))
    return vault_root


def _fts_sql(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='notes_fts'"
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else ""


def test_new_schema_declares_contentless_delete(vault):
    """Fresh index must carry `contentless_delete=1` — the concrete fix."""
    from lore_search.fts import FtsBackend, _db_path

    FtsBackend().reindex()

    sql = _fts_sql(_db_path())
    assert "contentless_delete" in sql, f"notes_fts missing contentless_delete: {sql!r}"


def test_reindex_end_to_end_returns_hits(vault):
    """Baseline: fresh reindex + search surfaces the seeded note."""
    from lore_search.fts import FtsBackend

    backend = FtsBackend()
    indexed = backend.reindex()
    assert indexed == 2

    hits = backend.search("HSM stepca")
    assert any("hsm-ceremony" in h.path for h in hits), hits


def test_reindex_auto_migrates_legacy_stock_contentless_db(vault):
    """A DB created by pre-fix lore (no `contentless_delete`) must NOT
    raise `cannot DELETE from contentless fts5 table` on the next
    reindex — connect-time migration drops the legacy virtual table so
    reindex rebuilds cleanly.
    """
    from lore_search.fts import FtsBackend, _db_path

    # Simulate the pre-fix on-disk state: create search.db with the
    # legacy schema, populate it, then point lore at it.
    db = _db_path()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wiki TEXT NOT NULL,
                path TEXT NOT NULL,
                filename TEXT NOT NULL,
                description TEXT,
                tags TEXT,
                repos TEXT,
                sha256 TEXT NOT NULL,
                mtime REAL NOT NULL,
                UNIQUE (wiki, path)
            );
            -- Legacy: contentless WITHOUT contentless_delete.
            CREATE VIRTUAL TABLE notes_fts USING fts5(
                title, description, tags, body,
                content='', tokenize='porter unicode61'
            );
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        # Seed a bogus row whose "real" file on disk has the same path —
        # reindex will try to UPDATE (which hits DELETE on notes_fts) and
        # crash unless the legacy table was migrated out.
        conn.execute(
            "INSERT INTO notes (wiki,path,filename,description,tags,repos,"
            "sha256,mtime) VALUES (?,?,?,?,?,?,?,?)",
            (
                "demo",
                "concepts/hsm-ceremony.md",
                "hsm-ceremony",
                "stale desc",
                "",
                "",
                "stale-sha-to-force-reupsert",
                0.0,
            ),
        )
        rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO notes_fts (rowid, title, description, tags, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (rowid, "stale title", "stale desc", "", "stale body"),
        )
        conn.commit()
    finally:
        conn.close()

    # Sanity-check that the legacy table is actually broken (guards
    # against the test silently lying if contentless-delete becomes
    # the SQLite default in some future release).
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="contentless"):
            conn.execute("DELETE FROM notes_fts WHERE rowid=?", (rowid,))
            conn.commit()
    finally:
        conn.close()

    # The real test: reindex must succeed without raising.
    backend = FtsBackend()
    backend.reindex()  # would raise without auto-migration

    # And search must work against the rebuilt index.
    hits = backend.search("stepca")
    assert any("hsm-ceremony" in h.path for h in hits), hits

    # And the migrated schema must carry contentless_delete=1.
    assert "contentless_delete" in _fts_sql(db)


def test_reindex_one_after_legacy_migration(vault):
    """`reindex_one` shares the _connect() path — it too must survive
    the legacy→current migration transparently."""
    from lore_search.fts import FtsBackend, _db_path

    db = _db_path()
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE VIRTUAL TABLE notes_fts USING fts5(
                title, description, tags, body,
                content='', tokenize='porter unicode61'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    backend = FtsBackend()
    note = vault / "wiki" / "demo" / "concepts" / "hsm-ceremony.md"
    backend.reindex_one(note)  # would raise without migration

    hits = backend.search("ceremony")
    assert any("hsm-ceremony" in h.path for h in hits), hits
