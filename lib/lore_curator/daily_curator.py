"""Curator B pipeline — cluster → abstract → file surfaces per wiki."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from lore_core.io import atomic_write_text
from lore_core.ledger import WikiLedger, WikiLedgerEntry
from lore_core.lockfile import LockContendedError, curator_lock
from lore_core.run_log import RunLogger
from lore_core.schema import parse_frontmatter
from lore_core.surfaces import SurfacesDoc, load_surfaces, load_surfaces_or_default
from lore_core.wiki_config import WikiConfig, load_wiki_config
from lore_curator.abstract import AbstractedSurface, abstract_cluster
from lore_curator.cluster import Cluster, cluster_session_notes
from lore_curator.surface_filer import FiledSurface, file_surface


@dataclass
class CuratorBResult:
    notes_considered: int = 0
    clusters_formed: int = 0
    surfaces_emitted: list[Path] = field(default_factory=list)
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0


def run_curator_b(
    *,
    lore_root: Path,
    wiki: str,                                      # required — Curator B is per-wiki
    llm_client: Any = None,
    dry_run: bool = False,
    now: datetime | None = None,
    since: datetime | None = None,                  # default = wiki_ledger.last_curator_b or now-3d
    lock_timeout: float = 0.0,                      # interactive callers pass >0 to wait
) -> CuratorBResult:
    """One Curator B pass over a single wiki.

    - Acquires curator lockfile.
    - Loads recent session notes from <lore_root>/wiki/<wiki>/sessions/.
    - Clusters them (middle tier).
    - Abstracts each cluster into surfaces (high tier; middle fallback if models.high == 'off').
    - Files each surface as draft:true via surface_filer.
    - Updates WikiLedger.last_curator_b.
    """
    start = time.monotonic()
    now = now or datetime.now(UTC)
    result = CuratorBResult()

    logger = RunLogger(
        lore_root,
        trigger="hook",
        role="b",
        config_snapshot={"wiki": wiki, "dry_run": dry_run},
        dry_run=dry_run,
    )

    with logger:
        wiki_root = lore_root / "wiki" / wiki
        if not wiki_root.exists():
            result.skipped_reasons["wiki_not_found"] = 1
            logger.emit("skip", reason="wiki_not_found", wiki=wiki)
            result.duration_seconds = time.monotonic() - start
            return result

        sessions_dir = wiki_root / "sessions"
        surfaces_doc = load_surfaces(wiki_root)
        if surfaces_doc is None:
            surfaces_doc = load_surfaces_or_default(wiki_root)
        elif not surfaces_doc.surfaces:
            result.skipped_reasons["surfaces_md_invalid"] = 1
            logger.emit("skip", reason="surfaces_md_invalid", wiki=wiki)
            result.duration_seconds = time.monotonic() - start
            return result

        if llm_client is None:
            result.skipped_reasons["no_anthropic_client"] = 1
            logger.emit("skip", reason="no_anthropic_client")
            result.duration_seconds = time.monotonic() - start
            return result

        cfg = load_wiki_config(wiki_root)

        def model_resolver(tier: str) -> str:
            return {"simple": cfg.models.simple, "middle": cfg.models.middle, "high": cfg.models.high}[tier]

        high_tier_off = cfg.models.high == "off"

        try:
            with curator_lock(lore_root, timeout=lock_timeout):
                wledger = WikiLedger(lore_root, wiki)
                wentry = wledger.read()
                cutoff = since or wentry.last_curator_b or (now - timedelta(days=3))

                # Phase D: threads.md is a derived view over ALL session
                # notes (not just the recent slice), so we regenerate
                # before any short-circuit. This way a fresh install
                # with only old notes — or a wiki that just had no work
                # since last cutoff — still gets a current threads.md.
                # The cluster/abstract/file work below is independent
                # and only acts on recent notes.
                #
                # We pass the client + simple-tier resolver so each
                # thread gets one cheap LLM call to produce a topical
                # heading. Falls back to file-basename labels if the
                # call fails or no model is configured.
                if not dry_run:
                    _regenerate_threads_md(
                        wiki_root, now=now, logger=logger,
                        llm_client=llm_client,
                        model_resolver=model_resolver,
                    )

                notes = _load_recent_session_notes(sessions_dir, cutoff=cutoff)
                result.notes_considered = len(notes)
                if not notes:
                    logger.emit("skip", reason="no_recent_notes", wiki=wiki, cutoff=str(cutoff))
                    if not dry_run:
                        _advance_wiki_ledger(wledger, wentry, now=now)
                    result.duration_seconds = time.monotonic() - start
                    return result

                surface_names = [s.name for s in surfaces_doc.surfaces]
                clusters = cluster_session_notes(
                    notes=notes,
                    surfaces=surface_names,
                    llm_client=llm_client,
                    model_resolver=model_resolver,
                )
                result.clusters_formed = len(clusters)
                for cluster in clusters:
                    logger.emit(
                        "cluster-formed",
                        topic=cluster.topic,
                        scope=cluster.scope,
                        suggested_surface=cluster.suggested_surface,
                        note_count=len(cluster.session_notes),
                    )

                sources_by_wl = _build_sources_map(notes)
                existing_surfaces = _load_existing_surfaces(wiki_root, surfaces_doc)

                for cluster in clusters:
                    abstracted = abstract_cluster(
                        cluster=cluster,
                        surfaces_doc=surfaces_doc,
                        source_notes_by_wikilink=sources_by_wl,
                        llm_client=llm_client,
                        model_resolver=model_resolver,
                        high_tier_off=high_tier_off,
                        lore_root=lore_root,
                        existing_surfaces=existing_surfaces,
                    )
                    for ab in abstracted:
                        if ab.merge_into:
                            # The LLM judged this cluster extends an existing
                            # surface — log it and skip filing. Curator C's
                            # defrag passes can act on the merge proposal.
                            logger.emit(
                                "merge-suggested",
                                surface_name=ab.surface_name,
                                title=ab.title,
                                merge_into=ab.merge_into,
                                source_notes=list(cluster.session_notes),
                            )
                            continue
                        if dry_run:
                            plural_dir = next(
                                (
                                    s.plural or (s.name if s.name.endswith("s") else f"{s.name}s")
                                    for s in surfaces_doc.surfaces
                                    if s.name == ab.surface_name
                                ),
                                f"{ab.surface_name}s",
                            )
                            result.surfaces_emitted.append(
                                wiki_root / plural_dir / f"<dry-run:{_short_slug(ab.title)}>.md"
                            )
                            continue
                        try:
                            filed = file_surface(
                                surface_name=ab.surface_name,
                                title=ab.title,
                                body=ab.body,
                                sources=cluster.session_notes,
                                wiki_root=wiki_root,
                                surfaces_doc=surfaces_doc,
                                extra_frontmatter=ab.extra_frontmatter,
                                now=now,
                            )
                            result.surfaces_emitted.append(filed.path)
                            logger.emit(
                                "surface-filed",
                                surface_name=ab.surface_name,
                                title=ab.title,
                                path=str(filed.path),
                            )
                        except ValueError as e:
                            reason = "surface_filer_validation"
                            result.skipped_reasons[reason] = result.skipped_reasons.get(reason, 0) + 1
                            logger.emit("skip", reason=reason, error=str(e))

                if not dry_run:
                    _advance_wiki_ledger(wledger, wentry, now=now)
        except LockContendedError:
            result.skipped_reasons["lock_contended"] = result.skipped_reasons.get("lock_contended", 0) + 1
            logger.emit("skip", reason="lock_contended")

    # Post-lock: auto-briefing (never fails the pipeline).
    if not dry_run and result.surfaces_emitted:
        try:
            _maybe_publish_briefing(
                lore_root=lore_root,
                wiki=wiki,
                wiki_config=cfg,
                now=now,
                dry_run=dry_run,
            )
        except Exception as exc:
            _curator_log(lore_root, f"_maybe_publish_briefing raised unexpectedly: {exc}")

    result.duration_seconds = time.monotonic() - start
    return result


# ---------- helpers ----------

def _load_recent_session_notes(sessions_dir: Path, *, cutoff: datetime) -> list[dict]:
    """Return list of {path, frontmatter, summary} for session notes touched since cutoff.

    `summary` = the first 800 chars of the note body (after frontmatter).
    `cutoff` is treated as tz-aware UTC; falls back to mtime if `created` frontmatter absent.
    """
    if not sessions_dir.exists():
        return []
    cutoff_ts = cutoff.timestamp()
    out: list[tuple[float, dict]] = []
    for p in sessions_dir.rglob("*.md"):
        try:
            text = p.read_text()
        except OSError:
            continue
        fm = parse_frontmatter(text) or {}
        # Use mtime as a robust ordering signal regardless of frontmatter quality.
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff_ts:
            # Also check `created` field — if it's recent enough, include even if mtime is old.
            created = fm.get("created")
            include = False
            if isinstance(created, str):
                try:
                    if datetime.fromisoformat(created).timestamp() >= cutoff_ts:
                        include = True
                except ValueError:
                    pass
            if not include:
                continue
        body = _strip_frontmatter(text)
        summary = body[:800]
        out.append((mtime, {
            "path": str(p),
            "wikilink": f"[[{p.stem}]]",
            "frontmatter": fm,
            "summary": summary,
            "body": body,
        }))
    out.sort(key=lambda r: r[0], reverse=True)
    return [d for _, d in out]


def _build_sources_map(notes: list[dict]) -> dict[str, str]:
    """wikilink → full body text, used by abstract step to inline source content."""
    return {n["wikilink"]: n.get("body", "") for n in notes}


def _load_existing_surfaces(
    wiki_root: Path, surfaces_doc: SurfacesDoc
) -> dict[str, list[dict]]:
    """Inventory existing surfaces per type so the abstract step can suggest merges.

    For each non-session surface declared in SURFACES.md, scan
    `<wiki_root>/<plural>/*.md` and collect `{wikilink, description}`
    pairs. Bodies are deliberately omitted — only titles + descriptions
    fit the LLM's judgement budget for "is this cluster already
    represented?". The session surface is skipped: sessions are
    Curator A's territory and listing them as merge candidates is a
    category error.

    Notes whose `type:` frontmatter doesn't match the surface name are
    skipped (defends against stray files dropped under e.g. concepts/).
    Standard skip-files (README.md, CLAUDE.md, _index.md, llms.txt, etc.)
    are excluded for consistency with the linter.

    Note: the inventory is captured ONCE at the start of run_curator_b.
    Surfaces filed during the same run are not added back to the
    inventory mid-loop, so a near-duplicate produced in cluster N+1 can't
    spot the surface produced in cluster N. The adjacent-merge defrag
    pass catches that case later. Keeping the cache outside the loop is
    intentional — re-walking the wiki per cluster would be quadratic.
    """
    from lore_core.lint import SKIP_FILES

    out: dict[str, list[dict]] = {}
    for surface_def in surfaces_doc.surfaces:
        if surface_def.name == "session":
            continue
        plural = surface_def.plural or (
            surface_def.name if surface_def.name.endswith("s") else f"{surface_def.name}s"
        )
        surface_dir = wiki_root / plural
        if not surface_dir.exists():
            continue
        items: list[dict] = []
        for p in sorted(surface_dir.rglob("*.md")):
            if p.name.startswith("_") or p.name in SKIP_FILES:
                continue
            try:
                fm = parse_frontmatter(p.read_text()) or {}
            except OSError:
                continue
            if fm.get("type") and fm.get("type") != surface_def.name:
                continue
            description = str(fm.get("description") or "").strip()
            items.append({
                "wikilink": f"[[{p.stem}]]",
                "description": description,
            })
        if items:
            out[surface_def.name] = items
    return out


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :].lstrip("\n")


def _advance_wiki_ledger(wledger: WikiLedger, entry: WikiLedgerEntry, *, now: datetime) -> None:
    entry.last_curator_b = now
    wledger.write(entry)


def _short_slug(title: str) -> str:
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:30] or "surface"


def _format_briefing_markdown(gather_result: dict[str, Any], *, wiki: str, now: datetime) -> str:
    """Render gather() output as a Markdown briefing string."""
    date_str = now.date().isoformat()
    lines: list[str] = [f"# Lore briefing — {wiki} · {date_str}", ""]

    new_sessions = gather_result.get("new_sessions", [])
    surfaces: list[dict] = []
    session_slugs: list[str] = []

    for sess in new_sessions:
        slug = sess.get("slug") or sess.get("path", "")
        session_slugs.append(slug)
        # Collect surfaces mentioned in frontmatter or sections.
        fm = sess.get("frontmatter") or {}
        desc = fm.get("description", "")
        title = fm.get("title", slug)
        surfaces.append({"wikilink": f"[[{sess.get('path', slug)}]]", "description": desc or title})

    if surfaces:
        lines.append("## New surfaces")
        for s in surfaces:
            lines.append(f"- {s['wikilink']} — {s['description']}")
        lines.append("")

    if session_slugs:
        lines.append("## Sessions covered")
        for sl in session_slugs:
            lines.append(f"- [[{sl}]]")
        lines.append("")

    return "\n".join(lines)


def _curator_log(lore_root: Path, message: str) -> None:
    """Append a timestamped line to <lore_root>/.lore/curator.log."""
    try:
        log_dir = lore_root / ".lore"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "curator.log"
        ts = datetime.now(UTC).isoformat()
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{ts}  {message}\n")
    except OSError:
        pass  # logging failure must never propagate


def _maybe_publish_briefing(
    *,
    lore_root: Path,
    wiki: str,
    wiki_config: WikiConfig,
    now: datetime,
    dry_run: bool,
) -> dict | None:
    """If auto-briefing is on, publish via configured sinks. Return None on skip,
    or a dict with {sinks_written: list[str]} on success.
    Errors are caught and logged; never re-raised.
    """
    if dry_run:
        return None

    briefing_cfg = wiki_config.briefing
    if not briefing_cfg.auto:
        return None

    try:
        from lore_core import briefing as _briefing_mod

        gather_result = _briefing_mod.gather(wiki=wiki)
        if "error" in gather_result:
            _curator_log(lore_root, f"briefing.gather error for wiki={wiki!r}: {gather_result['error']}")
            return None

        content = _format_briefing_markdown(gather_result, wiki=wiki, now=now)
        if not content.strip():
            return None

        sinks_written: list[str] = []
        for sink in briefing_cfg.sinks:
            try:
                if sink.startswith("markdown:"):
                    path_str = sink[len("markdown:"):]
                    out_path = Path(os.path.expanduser(path_str))
                    atomic_write_text(out_path, content)
                    sinks_written.append(sink)
                else:
                    sink_type = sink.split(":")[0] if ":" in sink else sink
                    _curator_log(lore_root, f"skipping unsupported sink type '{sink_type}' ({sink!r})")
            except Exception as exc:
                _curator_log(lore_root, f"briefing sink error for {sink!r}: {exc}")

        if sinks_written:
            # Update last_briefing on the wiki ledger.
            wledger = WikiLedger(lore_root, wiki)
            wentry = wledger.read()
            wentry.last_briefing = now
            wledger.write(wentry)

        return {"sinks_written": sinks_written}

    except Exception as exc:  # noqa: BLE001 - briefing publish wraps network/render/IO; failure must not break run
        _curator_log(lore_root, f"briefing auto-publish failed for wiki={wiki!r}: {exc}")
        return None


def _regenerate_threads_md(
    wiki_root: Path,
    *,
    now: datetime,
    logger: RunLogger | None = None,
    llm_client: Any = None,
    model_resolver: Any = None,
) -> None:
    """Rebuild ``<wiki>/threads.md`` from session-note frontmatter.

    Two-stage:
      1. Algorithmic — scan notes, group via union-find on shared files,
         render with a file-basename label as the section heading.
      2. Optional LLM enrichment — when an Anthropic-shaped client and
         a simple-tier model resolver are supplied, one cheap call per
         thread upgrades the heading to a topical label. Best-effort:
         a label-call failure preserves the algorithmic heading.

    Best-effort overall: any failure (parse error, OSError, surprise
    input shape) is swallowed and logged so a malformed note can't
    abort Curator B. The file is overwritten atomically; concurrent
    reads see either the old or the new full content, never a partial.
    """
    try:
        from lore_core.threads import (
            compute_threads,
            label_threads_with_llm,
            render_threads_markdown,
            scan_session_notes,
        )

        notes = scan_session_notes(wiki_root)
        threads = compute_threads(notes)
        if llm_client is not None and model_resolver is not None:
            threads = label_threads_with_llm(
                threads,
                llm_client=llm_client,
                model_resolver=model_resolver,
            )
        text = render_threads_markdown(
            threads, generated_at=now, notes_scanned=len(notes),
        )
        atomic_write_text(wiki_root / "threads.md", text)
        if logger is not None:
            logger.emit(
                "threads-regenerated",
                thread_count=len(threads),
                note_count=len(notes),
                llm_labels=sum(1 for t in threads if t.llm_label),
            )
    except Exception as exc:  # noqa: BLE001 - threads regen wraps file I/O + LLM labelling; never abort the curator pass
        if logger is not None:
            import traceback
            tb = traceback.format_exc()[:1000]
            logger.emit(
                "warning",
                reason="threads_regen_failed",
                error=str(exc)[:300],
                traceback=tb,
            )


# Role-name alias: ``run_daily_curator`` matches the module name;
# legacy ``run_curator_b`` kept as alias for existing call sites.
run_daily_curator = run_curator_b
