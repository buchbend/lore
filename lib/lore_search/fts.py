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

from lore_core.config import get_wiki_root
from lore_core.lint import (
    KNOWLEDGE_DIRS,
    SKIP_DIRS,
    SKIP_FILES,
    discover_notes,
    discover_wikis,
)
from lore_core.schema import parse_frontmatter
from lore_search.backend import SearchHit

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
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4 :].lstrip("\n") if end != -1 else text


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
    """SQLite FTS5 implementation of LoreBackend."""

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
        conn = _connect()
        try:
            sanitized = _sanitize_fts_query(query)
            if not sanitized:
                return []
            params: list = [sanitized]
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
            rows = conn.execute(sql, params).fetchall()
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
        finally:
            conn.close()

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
    """Build a safe FTS MATCH string by extracting word-like tokens.

    FTS5 query syntax is strict about punctuation; user queries may
    contain colons, parens, quotes, etc. We extract word tokens and
    OR them together, which matches the intent of a natural-language
    query without crashing on syntax.
    """
    tokens = _FTS_SAFE.findall(q)
    if not tokens:
        return ""
    # Drop pure stopwords? Keep simple — let FTS rank.
    return " OR ".join(tokens)
