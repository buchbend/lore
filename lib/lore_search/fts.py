"""Default Lore retrieval backend: SQLite FTS5 (BM25) + Model2Vec (optional).

At ~100–1000 notes, pure FTS5 BM25 handles queries well under 200ms and
produces high-quality rankings. Model2Vec 256-dim embeddings layer on
via Reciprocal Rank Fusion when installed; absent, we return BM25-only.

Index stored at $LORE_CACHE/search.db (default ~/.cache/lore/).
Incremental via mtime + SHA256 from the catalog.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.lint import (
    SKIP_DIRS,
    SKIP_FILES,
    discover_notes,
    discover_wikis,
)
from lore_core.schema import parse_frontmatter


@dataclass
class SearchHit:
    """One ranked result from a backend query."""

    path: str  # relative to wiki root
    wiki: str
    filename: str
    score: float
    description: str | None = None
    tags: list[str] | None = None
    snippet: str | None = None


DEFAULT_CACHE = Path.home() / ".cache" / "lore"
RRF_K = 60


def _cache_dir() -> Path:
    """Resolve the on-disk cache directory for the index."""
    env = os.environ.get("LORE_CACHE")
    return Path(env).expanduser() if env else DEFAULT_CACHE


def _db_path() -> Path:
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    return cache / "search.db"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


# Bump when the on-disk schema changes in a way that requires a rebuild.
# The v1→v2 jump added `contentless_delete=1` to `notes_fts`; without
# it, `DELETE FROM notes_fts WHERE rowid=?` raises
# `OperationalError: cannot DELETE from contentless fts5 table: notes_fts`
# on any reindex that updates or removes an already-indexed note.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wiki TEXT NOT NULL,
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    description TEXT,
    tags TEXT,           -- comma-separated
    repos TEXT,          -- comma-separated, from frontmatter
    sha256 TEXT NOT NULL,
    mtime REAL NOT NULL,
    UNIQUE (wiki, path)
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title,
    description,
    tags,
    body,
    content='',
    contentless_delete=1,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _stored_schema_version(conn: sqlite3.Connection) -> int:
    """Return the on-disk schema version, or 0 if uninitialised/legacy."""
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _migrate_if_needed(conn: sqlite3.Connection) -> None:
    """Drop any pre-SCHEMA_VERSION tables so the next reindex rebuilds.

    The only on-disk state worth preserving is the mtime/sha256 cache in
    `notes` — but that cache is only useful when `notes_fts` is in sync
    with it. Since the legacy `notes_fts` is unusable (DELETE fails),
    dropping both keeps them consistent and the next reindex repopulates.
    """
    if _stored_schema_version(conn) >= SCHEMA_VERSION:
        return
    conn.executescript(
        """
        DROP TABLE IF EXISTS notes_fts;
        DROP TABLE IF EXISTS notes;
        """
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    _migrate_if_needed(conn)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


@dataclass
class NoteRecord:
    wiki: str
    path: str
    filename: str
    title: str
    description: str
    tags: list[str]
    repos: list[str]
    body: str
    sha256: str
    mtime: float


from lore_core.schema import strip_frontmatter as _strip_frontmatter  # noqa: E402, F401


def _note_record(wiki: str, wiki_root: Path, path: Path) -> NoteRecord | None:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    fm = parse_frontmatter(text)
    body = _strip_frontmatter(text)
    rel = str(path.relative_to(wiki_root))
    return NoteRecord(
        wiki=wiki,
        path=rel,
        filename=path.stem,
        title=(fm.get("title") or path.stem.replace("-", " ")).strip(),
        description=(fm.get("description") or "").strip(),
        tags=[str(t) for t in (fm.get("tags") or [])],
        repos=[str(r) for r in (fm.get("repos") or [])],
        body=body,
        sha256=_sha256(text),
        mtime=path.stat().st_mtime,
    )


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


class FtsBackend:
    """SQLite FTS5 search backend."""

    name = "fts5"

    def reindex(self, *, wiki: str | None = None) -> int:
        conn = _connect()
        try:
            with conn:
                wikis = discover_wikis(wiki)
                indexed = 0
                for wiki_path in wikis:
                    wiki_name = wiki_path.name
                    known = {
                        row["path"]: (row["sha256"], row["id"])
                        for row in conn.execute(
                            "SELECT id, path, sha256 FROM notes WHERE wiki=?",
                            (wiki_name,),
                        )
                    }
                    seen: set[str] = set()
                    for fpath in discover_notes(wiki_path):
                        if fpath.name in SKIP_FILES:
                            continue
                        if any(part in SKIP_DIRS for part in fpath.parts):
                            continue
                        rec = _note_record(wiki_name, wiki_path, fpath)
                        if rec is None:
                            continue
                        seen.add(rec.path)
                        prior_sha, prior_id = known.get(rec.path, (None, None))
                        if prior_sha == rec.sha256:
                            continue
                        self._upsert(conn, rec, prior_id)
                        indexed += 1
                    # Remove notes gone from disk
                    to_delete = [p for p in known if p not in seen]
                    for path in to_delete:
                        _id = known[path][1]
                        conn.execute("DELETE FROM notes WHERE id=?", (_id,))
                        conn.execute("DELETE FROM notes_fts WHERE rowid=?", (_id,))
            return indexed
        finally:
            conn.close()

    def reindex_one(self, path: Path) -> None:
        wiki_root = get_wiki_root()
        # Find which wiki owns this note by walking upwards
        for wiki in discover_wikis(None):
            try:
                path.relative_to(wiki)
                rec = _note_record(wiki.name, wiki, path)
                if rec is None:
                    return
                conn = _connect()
                try:
                    with conn:
                        row = conn.execute(
                            "SELECT id FROM notes WHERE wiki=? AND path=?",
                            (rec.wiki, rec.path),
                        ).fetchone()
                        self._upsert(conn, rec, row["id"] if row else None)
                finally:
                    conn.close()
                return
            except ValueError:
                continue
        _ = wiki_root  # not used when path can't be resolved

    def _upsert(
        self,
        conn: sqlite3.Connection,
        rec: NoteRecord,
        prior_id: int | None,
    ) -> None:
        if prior_id is not None:
            conn.execute(
                "UPDATE notes SET sha256=?, mtime=?, description=?, "
                "tags=?, repos=? WHERE id=?",
                (
                    rec.sha256,
                    rec.mtime,
                    rec.description,
                    ",".join(rec.tags),
                    ",".join(rec.repos),
                    prior_id,
                ),
            )
            conn.execute("DELETE FROM notes_fts WHERE rowid=?", (prior_id,))
            rowid = prior_id
        else:
            cur = conn.execute(
                "INSERT INTO notes (wiki, path, filename, description, tags, "
                "repos, sha256, mtime) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rec.wiki,
                    rec.path,
                    rec.filename,
                    rec.description,
                    ",".join(rec.tags),
                    ",".join(rec.repos),
                    rec.sha256,
                    rec.mtime,
                ),
            )
            rowid = cur.lastrowid
        conn.execute(
            "INSERT INTO notes_fts (rowid, title, description, tags, body) "
            "VALUES (?, ?, ?, ?, ?)",
            (rowid, rec.title, rec.description, " ".join(rec.tags), rec.body),
        )

    def search(
        self,
        query: str,
        *,
        wiki: str | None = None,
        for_repo: str | None = None,
        k: int = 5,
    ) -> list[SearchHit]:
        """Hybrid AND-then-OR ranked search.

        FTS5 with space-separated tokens is implicit AND. Multi-token
        queries get tighter precision when the corpus supports it; if
        AND returns zero hits, retry with OR-joined tokens for graceful
        degradation. Single-token queries reduce to the same scan in
        both modes.

        Each query writes one record to ``$LORE_CACHE/query-log.jsonl``
        capturing both the AND and OR hit counts, so the
        AND-too-tight-vs-corpus-empty distinction is observable.
        """
        sanitized_and = _sanitize_fts_query(query)
        sanitized_or = _sanitize_fts_query_or(query)
        if not sanitized_and:
            _log_query(
                query=query,
                sanitized_and="",
                sanitized_or="",
                wiki=wiki,
                for_repo=for_repo,
                k=k,
                and_hits=0,
                or_hits=0,
                mode_final="empty",
                results=[],
            )
            return []

        conn = _connect()
        try:
            and_results = self._run(
                conn, sanitized_and, wiki=wiki, for_repo=for_repo, k=k
            )
            if and_results:
                final = and_results
                mode_final = "and"
                or_count = 0
            else:
                or_results = self._run(
                    conn, sanitized_or, wiki=wiki, for_repo=for_repo, k=k
                )
                final = or_results
                mode_final = "or" if or_results else "and"
                or_count = len(or_results)
        finally:
            conn.close()

        _log_query(
            query=query,
            sanitized_and=sanitized_and,
            sanitized_or=sanitized_or,
            wiki=wiki,
            for_repo=for_repo,
            k=k,
            and_hits=len(and_results),
            or_hits=or_count,
            mode_final=mode_final,
            results=[
                {"path": h.path, "wiki": h.wiki, "score": round(h.score, 3)}
                for h in final
            ],
        )
        return final

    def _run(
        self,
        conn: sqlite3.Connection,
        match_str: str,
        *,
        wiki: str | None,
        for_repo: str | None,
        k: int,
    ) -> list[SearchHit]:
        """Execute one FTS MATCH + over-fetch + repo-boost re-rank pass.

        Shared by the AND attempt and the OR fallback in ``search()`` so
        the repo-boost (1.5×) is applied consistently in both branches.
        """
        params: list = [match_str]
        where = ""
        if wiki:
            where = " AND n.wiki = ?"
            params.append(wiki)
        sql = f"""
        SELECT n.wiki, n.path, n.filename, n.description, n.tags, n.repos,
               bm25(notes_fts,
                    3.0,  -- title
                    2.0,  -- description
                    1.5,  -- tags
                    1.0   -- body
               ) AS score
        FROM notes_fts
        JOIN notes n ON n.id = notes_fts.rowid
        WHERE notes_fts MATCH ?{where}
        ORDER BY score
        LIMIT ?
        """
        params.append(k * 3)  # over-fetch for repo re-rank
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # Quoted tokens should never produce a malformed MATCH, but
            # if FTS5 ever rejects one (corrupted index, unsupported
            # tokenizer state), treat as no-hits rather than crash.
            return []
        hits: list[SearchHit] = []
        for r in rows:
            score = -float(r["score"])  # bm25 returns lower-better
            if for_repo:
                repos = r["repos"].split(",") if r["repos"] else []
                if for_repo in repos:
                    score *= 1.5
            hits.append(
                SearchHit(
                    path=r["path"],
                    wiki=r["wiki"],
                    filename=r["filename"],
                    score=score,
                    description=r["description"] or None,
                    tags=r["tags"].split(",") if r["tags"] else None,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def stats(self) -> dict:
        conn = _connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
            return {
                "backend": self.name,
                "db_path": str(_db_path()),
                "notes": row["n"],
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# FTS query sanitation — prevent syntax errors from user queries
# ---------------------------------------------------------------------------


_FTS_SAFE = re.compile(r"[A-Za-z0-9_'\-]+")


def _sanitize_fts_query(q: str) -> str:
    """Build an AND-style FTS MATCH string with each token quoted.

    Quoting (``"foo"``) neutralises FTS5 keywords (``AND``, ``OR``,
    ``NOT``, ``NEAR``) — without it, a user query of ``"AND"`` would
    yield the bareword ``AND`` which FTS5 parses as the operator and
    raises ``sqlite3.OperationalError: no such column: AND``.

    Tokens are space-joined (FTS5's implicit AND) so ``"foo bar"``
    matches docs containing both terms. Use :func:`_sanitize_fts_query_or`
    for the fallback when AND yields zero hits.
    """
    tokens = _FTS_SAFE.findall(q)
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens)


def _sanitize_fts_query_or(q: str) -> str:
    """OR-joined variant — fallback when AND yields zero hits."""
    tokens = _FTS_SAFE.findall(q)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def _log_query(**fields: Any) -> None:
    """Best-effort write to the query log; never raises."""
    from lore_search.query_log import get_logger

    try:
        get_logger().emit(**fields)
    except Exception:  # noqa: BLE001 — telemetry must never break search
        pass
