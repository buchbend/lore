"""Lore MCP server — exposes vault retrieval over the Model Context Protocol.

Runs as a local STDIO server. Any MCP client (Claude Desktop, Cursor,
Windsurf, Zed, etc.) can register this and query the vault.

Exposed tools:
    lore_search             — hybrid ranked search, top-k paths
    lore_read               — read one note by wiki/path
    lore_index              — return a wiki's _index.txt
    lore_catalog            — return a wiki's _catalog.json
    lore_resume             — unified context gather (recent/wiki/keyword/scope)
    lore_wikilinks          — in/out wikilinks for a note
    lore_drill              — composite multi-stage retrieval (search→read→
                              expand→read_expanded) in one envelope with a
                              structured trace
    lore_briefing_gather    — read-only briefing gather (new sessions since last
                              briefing + sink config + ledger); skill writes
                              prose, then shells out to publish + mark
    lore_inbox_classify     — read-only inbox walk (file list with type +
                              routing hint); skill composes notes, then shells
                              out to `lore inbox archive`
    lore_surface_context    — gather context pack for surface-authoring skills
    lore_surface_validate   — validate draft-spec + preview diff (no writes)

Start:
    lore mcp
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lore_core.config import get_wiki_root
from lore_core.errors import mcp_error as _mcp_error
from lore_core.freshness import compute_freshness, load_orphan_set, signal_to_dict
from lore_core.freshness_filter import apply_search_filter
from lore_core.schema import extract_wikilinks, parse_frontmatter
from lore_search.fts import FtsBackend

# ---------------------------------------------------------------------------
# Handlers (pure Python, usable by the MCP wrapper or a test harness)
# ---------------------------------------------------------------------------


# ``_mcp_error`` is re-exported from ``lore_core.errors``. Migration of the
# remaining bare-string ``{"error": "..."}`` returns under ``lore_core/`` to
# the structured envelope is complete. The JSON-RPC protocol-level error
# responses (``-32xxx`` codes) at the dispatcher use the JSON-RPC standard
# shape and are *not* this envelope — different layer, different contract.


def _resolve_wiki(wiki: str | None) -> Path | None:
    """Resolve a wiki name to its on-disk path."""
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return None
    if wiki:
        target = wiki_root / wiki
        return target if target.resolve().is_dir() else None
    # Single-wiki users: return the only one
    wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
    return wikis[0] if len(wikis) == 1 else None


def _resolve_current_handle(wiki_path: Path) -> str:
    """Best-effort current-user handle for personal-sidecar lookups.

    Empty string in solo wikis without `_users.yml` and without a
    configured git author is acceptable — callers treat ``""`` as
    "no personal record" and skip the sidecar lookup.
    """
    from lore_core.git import git_user_email
    from lore_core.identity import resolve_handle

    email = git_user_email(None, env_override="GIT_AUTHOR_EMAIL")
    return resolve_handle(wiki_path, email) if email else ""


def _freshness_block_for(
    wiki_path: Path,
    rel_path: str,
    *,
    orphan_set: set[Path] | None = None,
    sidecar_confirmed_at=None,
    handle: str | None = None,
) -> dict:
    """Compute the freshness block for a note hit.

    Centralised helper used by every retrieval surface so the wiring
    stays uniform. Returns a JSON-friendly dict with the four
    :class:`lore_core.freshness.FreshnessSignal` fields.

    Sidecar lookup precedence:
        1. ``sidecar_confirmed_at`` (caller already resolved it).
        2. Otherwise read from
           :func:`lore_core.verdicts_sidecar.get_confirmed` using
           ``handle`` (or the best-effort current handle when
           ``handle is None``).

    Best-effort: if the note can't be read (path-escape, missing file,
    etc.), returns a default ``confirmed`` block so callers never fail
    a retrieval over a freshness probe.
    """
    target = (wiki_path / rel_path).resolve()
    try:
        target.relative_to(wiki_path.resolve())
    except ValueError:
        return signal_to_dict(
            compute_freshness({}, target, wiki_path, None, set())
        )
    fm: dict = {}
    try:
        fm = parse_frontmatter(target.read_text(errors="replace"))
    except OSError:
        pass

    confirmed_at = sidecar_confirmed_at
    if confirmed_at is None:
        from lore_core.verdicts_sidecar import get_confirmed

        eff_handle = handle if handle is not None else _resolve_current_handle(wiki_path)
        if eff_handle:
            try:
                confirmed_at = get_confirmed(wiki_path, eff_handle, rel_path)
            except (OSError, ValueError):
                confirmed_at = None

    sig = compute_freshness(
        fm,
        target,
        wiki_path,
        confirmed_at,
        orphan_set or set(),
    )
    block = signal_to_dict(sig)

    # Slice 9: surface team-mode disagreements (someone marked stale,
    # someone else confirmed after) so the in-passing nudge can ask
    # for explicit resolution instead of silently overwriting.
    from lore_core.disagreement import detect_disagreement, disagreement_to_dict

    disagreement = detect_disagreement(fm, confirmed_at)
    if disagreement is not None:
        block["disagreement"] = disagreement_to_dict(disagreement)

    return block


# Time-based throttle for FTS reindexing in the long-lived MCP server.
# Reindex is already incremental (sha-compare per file) but still walks
# every note in the wiki on each call. Bursty agent traffic (Claude
# firing 5-10 lore_search calls in quick succession during a context
# gather) re-walks the same N notes each time. Skipping reindex when
# we've already reindexed this wiki within the throttle window
# ammortizes the cost across the burst.
#
# 5s is conservative — fresh edits made by the user mid-conversation
# show up on the next search after the throttle expires. For explicit
# re-index, use ``lore lint`` (which writes the catalog that
# ``reindex_one`` is based on).
#
# When the optional ``watchdog`` dependency is installed, the fs-watch
# daemon (``reindex_watcher``) bypasses the throttle by marking a wiki
# dirty whenever a note under it is created/modified/deleted. The next
# search re-indexes that wiki regardless of the throttle window. See
# ``docs/architecture/sync.md`` for the rationale.
_REINDEX_THROTTLE_S = 5.0
_reindex_last_seen: dict[str | None, float] = {}

from lore_mcp.reindex_watcher import ReindexDirtyState  # noqa: E402

_reindex_dirty = ReindexDirtyState()


def _maybe_reindex(backend: FtsBackend, wiki: str | None) -> None:
    """Throttled wrapper around ``backend.reindex``.

    Skips when this wiki was already reindexed within
    ``_REINDEX_THROTTLE_S`` seconds — *unless* the fs-watcher has marked
    the wiki dirty since the last reindex, in which case the throttle
    is bypassed.

    Skips emit a ``reindex_skip`` event to ``$LORE_CACHE/query-log.jsonl``
    so "feels stale" debugging ("why didn't my edit show up?") has a
    paper trail without code-reading.
    """
    import time as _time
    now = _time.monotonic()
    last = _reindex_last_seen.get(wiki)

    if last is not None and now - last < _REINDEX_THROTTLE_S:
        # Inside throttle window — only proceed if a real change was observed.
        if wiki is None or not _reindex_dirty.take(wiki):
            _log_reindex_skip(wiki, "throttle")
            return
    else:
        # Outside throttle window — always reindex; clear any pending dirty
        # flag so we don't double-reindex on the very next call.
        if wiki is not None:
            _reindex_dirty.take(wiki)

    backend.reindex(wiki=wiki)
    _reindex_last_seen[wiki] = now


def _log_reindex_skip(wiki: str | None, reason: str) -> None:
    """Best-effort emit to the shared query log; never raises."""
    try:
        from lore_search.query_log import get_logger

        get_logger().emit(event="reindex_skip", wiki=wiki, reason=reason)
    except Exception:  # noqa: BLE001 — telemetry must never break the hot path
        pass


def handle_search(
    query: str,
    wiki: str | None = None,
    for_repo: str | None = None,
    k: int = 5,
) -> list[dict[str, Any]]:
    backend = FtsBackend()
    _maybe_reindex(backend, wiki)
    hits = backend.search(query, wiki=wiki, for_repo=for_repo, k=k)
    # Cache one wiki-path resolution per (wiki-name) and one orphan-set
    # load per wiki-path to avoid re-walking ``$LORE_ROOT/wiki`` and
    # re-reading ``_catalog.json`` on every hit.
    wiki_path_cache: dict[str, Path | None] = {}
    orphan_cache: dict[str, set[Path]] = {}

    def _wp(name: str) -> Path | None:
        if name not in wiki_path_cache:
            wiki_path_cache[name] = _resolve_wiki(name)
        return wiki_path_cache[name]

    def _orphans(name: str, wp: Path) -> set[Path]:
        if name not in orphan_cache:
            orphan_cache[name] = load_orphan_set(wp)
        return orphan_cache[name]

    out: list[dict[str, Any]] = []
    for h in hits:
        wp = _wp(h.wiki)
        if wp is not None:
            freshness = _freshness_block_for(
                wp, h.path, orphan_set=_orphans(h.wiki, wp)
            )
        else:
            freshness = signal_to_dict(
                compute_freshness({}, Path(h.path), Path("/"), None, set())
            )
        out.append({
            "path": h.path,
            "wiki": h.wiki,
            "filename": h.filename,
            "score": round(h.score, 3),
            "description": h.description,
            "tags": h.tags or [],
            "freshness": freshness,
        })
    sorted_out, _audit = apply_search_filter(out)
    return sorted_out


def _resolve_slug(wiki_path: Path, slug: str) -> str | None:
    """Resolve a note slug to a relative path within the wiki.

    Three-tier lookup, fast → safe → last-resort:

    1. ``_catalog.json``'s top-level ``slug_index`` (O(1)) — populated
       by ``lore lint`` since Phase 1.2.
    2. Section iteration (O(n)) — fallback for catalogs written by
       pre-Phase-1.2 lint runs that don't have ``slug_index`` yet.
       Removed in v0.31.0 once every active wiki has rerun ``lore lint``.
    3. ``rglob`` walk — last resort for notes the catalog doesn't cover:
       freshly-written mid-session, drafts outside ``KNOWLEDGE_DIRS``,
       or files in ``inbox/`` (deliberately skipped by ``discover_notes``).
       Without this, ``lore_drill`` would silently drop wikilinks to
       brand-new notes.
    """
    cat_path = wiki_path / "_catalog.json"
    if cat_path.exists():
        try:
            catalog = json.loads(cat_path.read_text())
            slug_index = catalog.get("slug_index")
            if isinstance(slug_index, dict) and slug in slug_index:
                return slug_index[slug]
            # Tier 2: pre-Phase-1.2 catalogs lack slug_index — iterate sections.
            for entries in catalog.get("sections", {}).values():
                for entry in entries:
                    if entry["name"] == slug:
                        return entry["path"]
        except (json.JSONDecodeError, KeyError):
            pass
    # Tier 3: uncatalogued notes (drafts, inbox, freshly-written).
    candidates = list(wiki_path.rglob(f"{slug}.md"))
    if candidates:
        return str(candidates[0].relative_to(wiki_path))
    return None


def handle_read(
    path: str, wiki: str | None = None, section: str | None = None
) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return _mcp_error(
            "wiki_not_found",
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )

    # Resolve wikilink syntax or bare slug.
    slug = None
    if path.startswith("[[") and path.endswith("]]"):
        slug = path[2:-2]
    elif "/" not in path and not path.endswith(".md"):
        slug = path
    if slug:
        resolved = _resolve_slug(wiki_path, slug)
        if resolved is None:
            return _mcp_error("note_not_found", f"note not found: {slug}")
        path = resolved

    target = (wiki_path / path).resolve()
    try:
        target.relative_to(wiki_path.resolve())
    except ValueError:
        return _mcp_error("path_escape", "path escapes wiki root")
    if not target.exists():
        return _mcp_error("path_not_found", f"not found: {path}")
    text = target.read_text(errors="replace")

    freshness = _freshness_block_for(
        wiki_path, path, orphan_set=load_orphan_set(wiki_path)
    )

    if section is not None:
        section_text, headings = _extract_section(text, section)
        if section_text is None:
            if not headings:
                return _mcp_error(
                    "section_not_found",
                    f"section {section!r}: this note has no H2 sections",
                    next_="omit `section` to read the whole file",
                )
            return _mcp_error(
                "section_not_found",
                f"section {section!r} not found in {path}",
                next_=f"available H2 headings: {', '.join(headings)}",
            )
        return {
            "wiki": wiki_path.name,
            "path": path,
            "content": section_text,
            "section": section,
            "freshness": freshness,
        }

    return {
        "wiki": wiki_path.name,
        "path": path,
        "content": text,
        "freshness": freshness,
    }


def _extract_section(text: str, query: str) -> tuple[str | None, list[str]]:
    """Return ``(section_text, all_h2_headings)`` for the matched H2.

    Match rule: first H2 in document order whose heading is a
    case-insensitive substring of ``query`` (or vice-versa: ``query`` is
    a substring of the heading). Bounds are the ``## `` line through the
    next ``## `` line (exclusive) or EOF; nested H3+ are included.

    Code-fence aware: lines inside ` ``` ` or ``~~~`` fences are ignored
    when scanning for headings, so ``## not a heading`` inside a Python
    docstring example doesn't get treated as a section boundary.

    The returned ``all_h2_headings`` list is the document's full H2
    inventory (in order) so the MCP error envelope can name the
    available choices when ``query`` doesn't match.
    """
    needle = query.strip().lower()
    lines = text.splitlines()

    in_fence = False
    fence_char: str | None = None
    headings: list[tuple[int, str]] = []  # (line_index, heading_text)

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        # Track fence state. Match the opening sequence's char to the
        # closing sequence so ``` and ~~~ don't cross-terminate.
        if stripped.startswith(("```", "~~~")):
            char = "`" if stripped.startswith("```") else "~"
            if not in_fence:
                in_fence = True
                fence_char = char
            elif fence_char == char:
                in_fence = False
                fence_char = None
            continue
        if in_fence:
            continue
        if line.startswith("## ") and not line.startswith("### "):
            heading_text = line[3:].strip()
            headings.append((i, heading_text))

    all_heading_strs = [h for _, h in headings]
    if not headings:
        return None, []

    match_idx: int | None = None
    for idx, (_, heading_text) in enumerate(headings):
        h_lower = heading_text.lower()
        if needle in h_lower or h_lower in needle:
            match_idx = idx
            break
    if match_idx is None:
        return None, all_heading_strs

    start = headings[match_idx][0]
    end = headings[match_idx + 1][0] if match_idx + 1 < len(headings) else len(lines)
    return "\n".join(lines[start:end]), all_heading_strs


def handle_index(wiki: str | None = None) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return _mcp_error(
            "wiki_not_found",
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )
    index = wiki_path / "_index.txt"
    if not index.exists():
        return _mcp_error(
            "catalog_missing",
            "no _index.txt",
            next_="run `lore lint` to regenerate the index",
        )
    return {"wiki": wiki_path.name, "content": index.read_text(errors="replace")}


def handle_catalog(wiki: str | None = None) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return _mcp_error(
            "wiki_not_found",
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )
    cat = wiki_path / "_catalog.json"
    if not cat.exists():
        return _mcp_error(
            "catalog_missing",
            "no _catalog.json",
            next_="run `lore lint` to regenerate the catalog",
        )
    return json.loads(cat.read_text())


def handle_resume(
    wiki: str | None = None,
    days: int = 3,
    keyword: str | None = None,
    scope: str | None = None,
    k: int = 5,
) -> dict[str, Any]:
    """Unified resume gather. Delegates to lore_core.resume.gather().

    Modes (priority): scope > keyword > recent (wiki-scoped or all wikis).
    """
    from lore_core.resume import gather

    return gather(
        scope=scope,
        wiki=wiki,
        keyword=keyword,
        days=days,
        k=k,
    )


def handle_briefing_gather(
    wiki: str,
    since: str | None = None,
    include_body_sections: bool = True,
) -> dict[str, Any]:
    """Read-only briefing gather. Delegates to lore_core.briefing.gather()."""
    from lore_core.briefing import gather

    return gather(
        wiki=wiki, since=since, include_body_sections=include_body_sections
    )


def handle_inbox_classify() -> dict[str, Any]:
    """Read-only inbox classifier. Delegates to lore_core.inbox.classify()."""
    from lore_core.inbox import classify

    return classify()


def handle_journal_write(
    kind: str,
    text: str,
    author: str | None = None,
) -> dict[str, Any]:
    """Append a freeform entry to the AI or human journal."""
    from lore_core import journal

    if kind not in journal.VALID_KINDS:
        return _mcp_error(
            "invalid_kind",
            f"journal kind must be one of {journal.VALID_KINDS!r}, got {kind!r}",
        )
    text = (text or "").strip()
    if not text:
        return _mcp_error(
            "empty_entry",
            "journal entry text must be non-empty",
            next_="Pass a non-empty `text` arg.",
        )
    try:
        result = journal.write(kind, text, author=author)  # type: ignore[arg-type]
    except ValueError as e:
        return _mcp_error("invalid_entry", str(e))
    return {"schema": "lore.journal.write/1", "data": result}


def handle_journal_read(
    kind: str = "ai",
    limit: int = 10,
) -> dict[str, Any]:
    """Read recent entries from the AI or human journal (newest-first)."""
    from lore_core import journal

    if kind not in journal.VALID_KINDS:
        return _mcp_error(
            "invalid_kind",
            f"journal kind must be one of {journal.VALID_KINDS!r}, got {kind!r}",
        )
    entries = journal.read(kind, limit=limit)  # type: ignore[arg-type]
    return {
        "schema": "lore.journal.read/1",
        "data": {"kind": kind, "entries": entries},
    }


def handle_surface_context(wiki: str) -> dict[str, Any]:
    """Gather context pack for surface-authoring skills."""
    from importlib import resources
    from lore_core.surfaces import load_surfaces
    import yaml

    wiki_dir = _resolve_wiki(wiki)
    if wiki_dir is None:
        return {
            "schema": "lore.surface.context/1",
            "wiki": wiki,
            "error": f"wiki '{wiki}' not found under $LORE_ROOT/wiki/",
        }

    surfaces_path = wiki_dir / "SURFACES.md"
    exists = surfaces_path.exists()
    doc = load_surfaces(wiki_dir) if exists else None
    current: list[dict[str, Any]] = []
    note_samples: dict[str, list[str]] = {}

    if doc is not None:
        for s in doc.surfaces:
            current.append({
                "name": s.name,
                "description": s.description,
                "required": list(s.required),
                "optional": list(s.optional),
                "extract_when": s.extract_when,
                "plural": s.plural,
                "slug_format": s.slug_format,
                "extract_prompt": s.extract_prompt,
            })
            dirname = s.plural or (s.name if s.name.endswith("s") else f"{s.name}s")
            subdir = wiki_dir / dirname
            if not subdir.is_dir():
                continue
            samples: list[tuple[str, str]] = []
            for md in subdir.glob("*.md"):
                try:
                    txt = md.read_text()
                except OSError:
                    continue
                fm = parse_frontmatter(txt)
                if not fm:
                    continue
                created = str(fm.get("created", ""))
                samples.append((created, md.stem))
            samples.sort(reverse=True)
            if samples:
                note_samples[s.name] = [f"[[{stem}]]" for _created, stem in samples[:3]]

    shipped_templates: dict[str, str] = {}
    for tmpl in ("standard", "science", "design"):
        try:
            shipped_templates[tmpl] = (
                resources.files("lore_core.surface_templates")
                .joinpath(f"{tmpl}.md")
                .read_text()
            )
        except (FileNotFoundError, ModuleNotFoundError):
            continue

    claude_md_attach = ""
    claude_md = wiki_dir / "CLAUDE.md"
    if claude_md.exists():
        txt = claude_md.read_text()
        start = txt.find("## Lore")
        if start != -1:
            end = txt.find("\n## ", start + 1)
            claude_md_attach = txt[start:end] if end != -1 else txt[start:]

    return {
        "schema": "lore.surface.context/1",
        "wiki": wiki,
        "wiki_dir": str(wiki_dir),
        "surfaces_md_exists": exists,
        "current_surfaces": current,
        "claude_md_attach": claude_md_attach,
        "note_samples": note_samples,
        "shipped_templates": shipped_templates,
    }


def handle_surface_validate(wiki: str, draft: dict) -> dict[str, Any]:
    """Validate a draft-spec. Returns issues + rendered markdown + unified diff."""
    import difflib
    from lore_core.surfaces import (
        SurfaceDef,
        render_section,
        render_document,
        validate_draft,
    )

    wiki_dir = _resolve_wiki(wiki)
    if wiki_dir is None:
        return {
            "schema": "lore.surface.validate/1",
            "ok": False,
            "issues": [{
                "level": "error",
                "code": "unknown_wiki",
                "message": f"wiki '{wiki}' not found under $LORE_ROOT/wiki/",
            }],
            "rendered_markdown": "",
            "diff_preview": "",
        }

    issues = validate_draft(draft, wiki_dir=wiki_dir)
    ok = not any(i["level"] == "error" for i in issues)

    rendered = ""
    op = draft.get("operation")
    surfaces_path = wiki_dir / "SURFACES.md"
    current_text = surfaces_path.read_text() if surfaces_path.exists() else ""
    new_text = current_text

    try:
        if op == "append" and isinstance(draft.get("surface"), dict):
            s = draft["surface"]
            sd = SurfaceDef(
                name=s.get("name", ""),
                description=s.get("description", ""),
                required=list(s.get("required") or []),
                optional=list(s.get("optional") or []),
                extract_when=s.get("extract_when", ""),
                plural=s.get("plural"),
                slug_format=s.get("slug_format"),
                extract_prompt=s.get("extract_prompt"),
            )
            rendered = render_section(sd)
            if current_text:
                new_text = current_text.rstrip("\n") + "\n\n" + rendered
            else:
                new_text = "# Surfaces\nschema_version: 2\n\n" + rendered
        elif op == "init" and isinstance(draft.get("surfaces"), list):
            sds = [
                SurfaceDef(
                    name=s.get("name", ""),
                    description=s.get("description", ""),
                    required=list(s.get("required") or []),
                    optional=list(s.get("optional") or []),
                    extract_when=s.get("extract_when", ""),
                    plural=s.get("plural"),
                    slug_format=s.get("slug_format"),
                    extract_prompt=s.get("extract_prompt"),
                )
                for s in draft["surfaces"]
            ]
            new_text = render_document(
                schema_version=draft.get("schema_version", 2),
                surfaces=sds,
                wiki=wiki,
            )
            rendered = new_text
    except Exception as e:
        issues.append({
            "level": "error",
            "code": "render_failed",
            "message": str(e),
        })
        ok = False

    diff_lines = list(difflib.unified_diff(
        current_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="a/SURFACES.md",
        tofile="b/SURFACES.md",
    ))
    diff_preview = "".join(diff_lines)

    return {
        "schema": "lore.surface.validate/1",
        "wiki": wiki,
        "ok": ok,
        "issues": issues,
        "rendered_markdown": rendered,
        "diff_preview": diff_preview,
    }


def handle_drill(
    query: str,
    wiki: str | None = None,
    k: int = 5,
    expand_limit: int = 5,
    expand_only: list[str] | None = None,
) -> dict[str, Any]:
    """Composite multi-stage retrieval: search → read → expand → read_expanded.

    Returns one envelope ``{"trace": [...], "result": {"notes": [...]}}`` so
    callers get the full chain in a single round-trip. Each stage records
    ``elapsed_ms`` and a stage-specific summary; empty intermediate results
    short-circuit downstream stages and are recorded with a ``skipped`` reason
    so the LLM/human can see *why* a stage was skipped without re-running.

    When the cap fires, the ``read_expanded`` stage records the dropped
    slugs in ``truncated_slugs`` so the agent can re-call with
    ``expand_only=[...]`` to read exactly those links without recomputing
    the search/expand stages. ``expand_only`` is intersection-only: it
    cannot add slugs that weren't in the discovered set.

    See ``docs/architecture/lore-drill.md`` for the full design.
    """
    import time as _time

    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return _mcp_error(
            "wiki_not_found",
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )

    trace: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []

    # Stage 1: search
    t0 = _time.monotonic()
    hits = handle_search(query=query, wiki=wiki, k=k)
    trace.append({
        "stage": "search",
        "query": query,
        "hits": len(hits),
        "elapsed_ms": int((_time.monotonic() - t0) * 1000),
    })

    if not hits:
        # Record the downstream stages as skipped so the trace shape is
        # uniform — easier for clients to parse than missing entries.
        for stage in ("read", "expand", "read_expanded"):
            trace.append({"stage": stage, "skipped": "search_returned_zero", "elapsed_ms": 0})
        return {"trace": trace, "result": {"notes": notes}}

    # Stage 2: read top hits
    t0 = _time.monotonic()
    top_paths = [h["path"] for h in hits]
    read_failed_top: list[str] = []
    for path in top_paths:
        body = handle_read(path=path, wiki=wiki)
        if "error" in body:
            read_failed_top.append(path)
            continue
        notes.append(body)
    read_step: dict[str, Any] = {
        "stage": "read",
        "paths": top_paths,
        "elapsed_ms": int((_time.monotonic() - t0) * 1000),
    }
    if read_failed_top:
        read_step["read_failed"] = read_failed_top
    trace.append(read_step)

    # Stage 3: expand wikilinks
    t0 = _time.monotonic()
    seen: set[str] = set()
    expanded_slugs: list[str] = []
    for note in notes:
        for slug in extract_wikilinks(note.get("content", "")):
            if slug not in seen:
                seen.add(slug)
                expanded_slugs.append(slug)
    if not expanded_slugs:
        trace.append({"stage": "expand", "skipped": "no_wikilinks", "elapsed_ms": int((_time.monotonic() - t0) * 1000)})
        trace.append({"stage": "read_expanded", "skipped": "no_wikilinks", "elapsed_ms": 0})
        return {"trace": trace, "result": {"notes": notes}}
    expand_step: dict[str, Any] = {
        "stage": "expand",
        "wikilinks": expanded_slugs,
        "elapsed_ms": int((_time.monotonic() - t0) * 1000),
    }
    # Apply expand_only filter (intersection with discovered set).
    if expand_only is not None:
        keep = set(expand_only)
        filtered = [s for s in expanded_slugs if s in keep]
        expand_step["expand_only"] = list(expand_only)
        expand_step["filtered_to"] = filtered
        expanded_slugs = filtered
    trace.append(expand_step)

    if not expanded_slugs:
        # `expand_only` filtered everything out — record skipped + return.
        trace.append({"stage": "read_expanded", "skipped": "expand_only_empty", "elapsed_ms": 0})
        return {"trace": trace, "result": {"notes": notes}}

    # Stage 4: read expanded (cap at expand_limit, skip unresolvable slugs).
    # `truncated` only applies when we actually hit the cap — an unresolvable
    # slug is NOT a "truncation" (the candidate set was just smaller than it
    # looked). `expand.wikilinks` records the discovery set;
    # `read_expanded.paths` records what was actually read. Clients reading
    # the trace must look at the right stage to answer "did we see X?" vs.
    # "did we read X?".
    t0 = _time.monotonic()
    resolved_paths: list[str] = []
    read_failed_expanded: list[str] = []
    cap_triggered_at: int | None = None
    for idx, slug in enumerate(expanded_slugs):
        if len(resolved_paths) >= expand_limit:
            cap_triggered_at = idx
            break
        rel = _resolve_slug(wiki_path, slug)
        if rel is None:
            continue
        body = handle_read(path=rel, wiki=wiki)
        if "error" in body:
            read_failed_expanded.append(rel)
            continue
        notes.append(body)
        resolved_paths.append(rel)
    trace_step: dict[str, Any] = {
        "stage": "read_expanded",
        "paths": resolved_paths,
        "elapsed_ms": int((_time.monotonic() - t0) * 1000),
    }
    if cap_triggered_at is not None:
        # Only count truncation when the cap actually stopped us, not when
        # the candidate set was simply smaller than expand_limit.
        trace_step["truncated"] = len(expanded_slugs) - cap_triggered_at
        trace_step["truncated_slugs"] = expanded_slugs[cap_triggered_at:]
        trace_step["kept"] = expand_limit
    if read_failed_expanded:
        trace_step["read_failed"] = read_failed_expanded
    trace.append(trace_step)

    return {"trace": trace, "result": {"notes": notes}}


def handle_verdict(
    wiki: str,
    note: str,
    verdict: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Slice 5 + 6: write a freshness verdict for a note.

    Slice 5 wires the ``stale`` branch (frontmatter additive write of
    the four ``status:stale`` fields). The ``confirm`` branch returns
    a structured "not yet implemented in this slice" error pointing
    at slice 6.

    Returns the post-verdict :class:`FreshnessSignal` for the note so
    the caller can confirm what changed.
    """
    from lore_core.identity import resolve_handle
    from lore_core.git import git_user_email
    from lore_core.stale_marker_writer import (
        StaleMarkerError,
        clear_stale,
        mark_stale,
    )

    if verdict not in {"stale", "confirm", "clear-stale"}:
        return _mcp_error(
            "invalid_verdict",
            f"verdict must be one of stale|confirm|clear-stale, got {verdict!r}",
        )

    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return _mcp_error(
            "wiki_not_found",
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )

    # Resolve note path the same way handle_read does.
    slug = None
    if note.startswith("[[") and note.endswith("]]"):
        slug = note[2:-2]
    elif "/" not in note and not note.endswith(".md"):
        slug = note
    rel_path = note
    if slug:
        resolved = _resolve_slug(wiki_path, slug)
        if resolved is None:
            return _mcp_error("note_not_found", f"note not found: {slug}")
        rel_path = resolved
    target = (wiki_path / rel_path).resolve()
    try:
        target.relative_to(wiki_path.resolve())
    except ValueError:
        return _mcp_error("path_escape", "path escapes wiki root")
    if not target.exists():
        return _mcp_error("path_not_found", f"not found: {rel_path}")

    if verdict == "confirm":
        from lore_core.verdicts_sidecar import set_confirmed

        email = git_user_email(None, env_override="GIT_AUTHOR_EMAIL")
        handle = resolve_handle(wiki_path, email) if email else ""
        if not handle:
            return _mcp_error(
                "no_handle",
                "could not resolve current handle for personal confirm",
                next_="set GIT_AUTHOR_EMAIL or configure git user.email",
            )
        try:
            written = set_confirmed(wiki_path, handle, rel_path)
        except (OSError, ValueError) as e:
            return _mcp_error("confirm_write_failed", str(e))
        freshness = _freshness_block_for(
            wiki_path,
            rel_path,
            orphan_set=load_orphan_set(wiki_path),
            sidecar_confirmed_at=written,
            handle=handle,
        )
        return {
            "schema": "lore.verdict/1",
            "wiki": wiki_path.name,
            "path": rel_path,
            "verdict": "confirm",
            "confirmed_at": written.isoformat(),
            "freshness": freshness,
        }

    if verdict == "stale":
        if not reason or not str(reason).strip():
            return _mcp_error(
                "reason_required",
                "verdict=stale requires a non-empty `reason`",
                next_="describe in one short line why the note is stale",
            )
        email = git_user_email(None, env_override="GIT_AUTHOR_EMAIL")
        handle = resolve_handle(wiki_path, email) if email else ""
        try:
            mark_stale(target, reason=str(reason), handle=handle)
        except StaleMarkerError as e:
            return _mcp_error("stale_write_refused", str(e))
        freshness = _freshness_block_for(
            wiki_path, rel_path, orphan_set=load_orphan_set(wiki_path)
        )
        return {
            "schema": "lore.verdict/1",
            "wiki": wiki_path.name,
            "path": rel_path,
            "verdict": "stale",
            "freshness": freshness,
        }

    # verdict == "clear-stale"
    clear_stale(target)
    freshness = _freshness_block_for(
        wiki_path, rel_path, orphan_set=load_orphan_set(wiki_path)
    )
    return {
        "schema": "lore.verdict/1",
        "wiki": wiki_path.name,
        "path": rel_path,
        "verdict": "clear-stale",
        "freshness": freshness,
    }


def handle_wikilinks(note: str, wiki: str | None = None) -> dict[str, Any]:
    wiki_path = _resolve_wiki(wiki)
    if wiki_path is None:
        return _mcp_error(
            "wiki_not_found",
            f"wiki not found: {wiki}",
            next_="run `lore status` to list configured wikis",
        )
    cat_path = wiki_path / "_catalog.json"
    if not cat_path.exists():
        return _mcp_error(
            "catalog_missing",
            "no _catalog.json",
            next_="run `lore lint` to regenerate the catalog",
        )
    catalog = json.loads(cat_path.read_text())
    for entries in catalog.get("sections", {}).values():
        for entry in entries:
            if entry["name"] == note or entry["path"] == note:
                return {
                    "wiki": wiki_path.name,
                    "note": entry["name"],
                    "links_out": entry.get("links_out", []),
                    "links_in": entry.get("links_in", []),
                }
    # Fall back to live parse
    candidates = list(wiki_path.rglob(f"{note}.md"))
    if candidates:
        text = candidates[0].read_text(errors="replace")
        return {
            "wiki": wiki_path.name,
            "note": note,
            "links_out": extract_wikilinks(text),
            "links_in": [],
            "note_missing_from_catalog": True,
        }
    return _mcp_error("note_not_found", f"note not found: {note}")


# ---------------------------------------------------------------------------
# MCP server wrapper
# ---------------------------------------------------------------------------


def _tool_schema() -> list[dict]:
    return [
        {
            "name": "lore_search",
            "description": (
                "Hybrid ranked search across the vault's knowledge notes. "
                "Returns top-k paths with descriptions and scores."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "wiki": {"type": "string", "description": "Scope to one wiki (optional)"},
                    "for_repo": {
                        "type": "string",
                        "description": "Boost notes tagged with this repo (org/name)",
                    },
                    "k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "lore_read",
            "description": (
                "Read one note by relative path, [[wikilink]], or bare slug "
                "within a wiki. Optional `section` arg returns just one H2 "
                "section (first match in document order, case-insensitive "
                "substring; code-fence aware so ## inside fenced blocks is "
                "ignored). Use `section` for long surface notes when you only "
                "need one heading's content."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "wiki": {"type": "string"},
                    "section": {
                        "type": "string",
                        "description": (
                            "If set, return only the matching H2 section "
                            "(heading + body to next H2 or EOF)."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "lore_index",
            "description": "Return the wiki's _index.txt (LLM-scannable knowledge map; markdown body with wikilinks).",
            "inputSchema": {
                "type": "object",
                "properties": {"wiki": {"type": "string"}},
            },
        },
        {
            "name": "lore_catalog",
            "description": "Return the wiki's _catalog.json (full machine-readable metadata + link graph).",
            "inputSchema": {
                "type": "object",
                "properties": {"wiki": {"type": "string"}},
            },
        },
        {
            "name": "lore_resume",
            "description": (
                "Load working context from the vault. Modes (priority "
                "order): scope > keyword > recent. Returns a structured "
                "dict with `mode` discriminator. Use at session start or "
                "any time the agent needs broader context without "
                "iterating through Glob/Read."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Scope prefix to aggregate gh issues + PRs + sessions for (e.g. ccat:data-center)",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "FTS5 ranked search across the vault",
                    },
                    "wiki": {
                        "type": "string",
                        "description": "Restrict to one wiki (default: all wikis for recent mode)",
                    },
                    "days": {
                        "type": "integer",
                        "default": 3,
                        "description": "Recency window for sessions (recent mode only)",
                    },
                    "k": {
                        "type": "integer",
                        "default": 5,
                        "description": "Top-k results for keyword search",
                    },
                },
            },
        },
        {
            "name": "lore_wikilinks",
            "description": "Return incoming and outgoing [[wikilinks]] for a note (graph traversal).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                    "wiki": {"type": "string"},
                },
                "required": ["note"],
            },
        },
        {
            "name": "lore_drill",
            "description": (
                "Composite multi-stage retrieval: search → read top hits → expand "
                "wikilinks → read expanded set, all in one round-trip. Returns "
                "{trace: [...], result: {notes: [...]}} with structured stage "
                "breadcrumbs (elapsed_ms per stage; `skipped` reasons for empty "
                "intermediate results: `search_returned_zero`, `no_wikilinks`, "
                "`expand_only_empty`). When the expand cap fires, the "
                "`read_expanded` stage records `truncated_slugs: [...]` listing "
                "the dropped links — re-call with `expand_only=[slugs]` to read "
                "exactly those without recomputing search. `expand_only` is "
                "intersection-only (it cannot add slugs that weren't in the "
                "discovered set; only narrow). "
                "Prefer `lore_drill` for cold-start exploration of a topic. "
                "Prefer `lore_search` (then `lore_read`) when you already know "
                "the rough path/slug or want to steer between stages."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "wiki": {"type": "string", "description": "Scope to one wiki (optional)"},
                    "k": {"type": "integer", "default": 5, "description": "Top-k for the search stage"},
                    "expand_limit": {
                        "type": "integer",
                        "default": 5,
                        "description": "Max number of expanded notes to read (cap on hub-note blowup)",
                    },
                    "expand_only": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Intersect the discovered wikilinks with this list "
                            "before stage 4. Use to re-drill specific slugs from "
                            "a prior call's `truncated_slugs`."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
        {
            "name": "lore_briefing_gather",
            "description": (
                "Read-only briefing gather: returns the new session "
                "notes (since the last briefing) plus the wiki's sink "
                "config and ledger state. Caller composes the briefing "
                "prose, then shells out to `lore briefing publish` and "
                "`lore briefing mark`. No LLM call inside the tool."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "wiki": {"type": "string"},
                    "since": {
                        "type": "string",
                        "description": "ISO date floor (YYYY-MM-DD)",
                    },
                    "include_body_sections": {
                        "type": "boolean",
                        "default": True,
                        "description": "Extract H2 sections per session",
                    },
                },
                "required": ["wiki"],
            },
        },
        {
            "name": "lore_inbox_classify",
            "description": (
                "Read-only inbox walk: returns every file in the root "
                "inbox and per-wiki inboxes with detected type and "
                "routing hint. Caller reads each file, composes vault "
                "notes (LLM judgment), then runs `lore inbox archive` "
                "to move the source to `.processed/`."
            ),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "lore_surface_context",
            "description": (
                "Gather context for surface-authoring skills: current SURFACES.md, "
                "CLAUDE.md attach block, sampled recent notes per surface, shipped templates."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"wiki": {"type": "string"}},
                "required": ["wiki"],
            },
        },
        {
            "name": "lore_surface_validate",
            "description": (
                "Validate a surface draft-spec (append or init). Returns structured "
                "issue list + rendered markdown + unified diff preview against the "
                "current SURFACES.md. Never writes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "wiki": {"type": "string"},
                    "draft": {"type": "object"},
                },
                "required": ["wiki", "draft"],
            },
        },
        {
            "name": "lore_journal_write",
            "description": (
                "Append a freeform entry to the AI or human journal "
                "(newest-first). The AI journal is YOUR space — write "
                "observations about workflow, criticism, half-formed "
                "ideas, jokes, weather, anything that would otherwise "
                "be lost. The bar is *would this be lost otherwise*, "
                "not *does this serve the user*. Don't write filler. "
                "Available iff `journal.enabled` in the root config."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["ai", "human"],
                        "description": "Which journal to write to.",
                    },
                    "text": {
                        "type": "string",
                        "description": "The entry body (plain markdown).",
                    },
                    "author": {
                        "type": "string",
                        "description": "Override the auto-resolved author tag.",
                    },
                },
                "required": ["kind", "text"],
            },
        },
        {
            "name": "lore_journal_read",
            "description": (
                "Read recent entries from the AI or human journal "
                "(newest-first). Use sparingly — the journal is for "
                "*writing*, not for self-referential reading."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["ai", "human"],
                        "default": "ai",
                    },
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
        {
            "name": "lore_verdict",
            "description": (
                "Record a freshness verdict for a note. Use when the "
                "user replies to an in-passing freshness nudge with a "
                "concrete answer: \"yes still good\" → "
                "verdict=\"confirm\"; \"no, stale because X\" → "
                "verdict=\"stale\" with a one-line `reason`. The "
                "stale branch writes the four-field schema "
                "(`status: stale`, `stale_reason`, `stale_by`, "
                "`stale_at`) additively to the note's frontmatter; "
                "never touches the body. The verdict is silent until "
                "the user actually responds — if the user types past "
                "the nudge without answering, do NOT call this tool. "
                "(`clear-stale` removes a prior stale verdict; the "
                "confirm branch lands in slice 6.)"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "wiki": {"type": "string"},
                    "note": {
                        "type": "string",
                        "description": "Note path, [[wikilink]], or bare slug.",
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["confirm", "stale", "clear-stale"],
                    },
                    "reason": {
                        "type": "string",
                        "description": "Required when verdict=stale.",
                    },
                },
                "required": ["wiki", "note", "verdict"],
            },
        },
    ]


def _dispatch(tool_name: str, args: dict) -> Any:
    # `/lore:off` (scope=all) — refuse every tool for this session.
    # See `docs/architecture/slash-toggles.md`.
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        from lore_core.toggles import is_off
        if is_off("all", sid):
            return _mcp_error(
                "session_off",
                "Lore is muted for this session.",
                next_="Run `lore on` from a shell in this session, or restart the session.",
            )

    match tool_name:
        case "lore_search":
            return handle_search(**args)
        case "lore_read":
            return handle_read(**args)
        case "lore_index":
            return handle_index(**args)
        case "lore_catalog":
            return handle_catalog(**args)
        case "lore_resume":
            return handle_resume(**args)
        case "lore_wikilinks":
            return handle_wikilinks(**args)
        case "lore_drill":
            return handle_drill(**args)
        case "lore_briefing_gather":
            return handle_briefing_gather(**args)
        case "lore_inbox_classify":
            return handle_inbox_classify(**args)
        case "lore_surface_context":
            return handle_surface_context(**args)
        case "lore_surface_validate":
            return handle_surface_validate(**args)
        case "lore_journal_write":
            return handle_journal_write(**args)
        case "lore_journal_read":
            return handle_journal_read(**args)
        case "lore_verdict":
            return handle_verdict(**args)
        case _:
            return _mcp_error("unknown_tool", f"unknown tool: {tool_name}")


def _start_reindex_watcher() -> None:
    """Start the optional fs-watch daemon that invalidates the reindex throttle.

    No-op if ``watchdog`` isn't installed or the lore root is missing.
    Called once at MCP-server boot. Daemon thread; exits with the process.
    """
    from lore_core.config import get_lore_root
    from lore_mcp.reindex_watcher import start_watcher

    try:
        lore_root = get_lore_root()
    except Exception:  # noqa: BLE001 — never fail boot for telemetry
        return
    start_watcher(lore_root, _reindex_dirty)


def start_server() -> int:
    """Start the MCP STDIO server using the official ``mcp`` Python SDK.

    The SDK is a hard dependency (declared in ``pyproject.toml``); if the
    import fails the install is broken and we propagate the ImportError
    rather than silently degrading to a half-implemented fallback.
    """
    _start_reindex_watcher()

    import asyncio

    from mcp.server import Server  # type: ignore[import-untyped]
    from mcp.server.stdio import stdio_server  # type: ignore[import-untyped]
    from mcp.types import TextContent, Tool  # type: ignore[import-untyped]

    server = Server("lore")
    schema = _tool_schema()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=s["name"],
                description=s["description"],
                inputSchema=s["inputSchema"],
            )
            for s in schema
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[TextContent]:
        result = _dispatch(name, arguments or {})
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())
    return 0
