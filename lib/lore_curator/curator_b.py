"""Curator B pipeline — cluster → abstract → file surfaces per wiki."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from lore_core.ledger import WikiLedger, WikiLedgerEntry
from lore_core.lockfile import LockContendedError, curator_lock
from lore_core.schema import parse_frontmatter
from lore_core.surfaces import SurfacesDoc, load_surfaces, load_surfaces_or_default
from lore_core.wiki_config import load_wiki_config
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
    anthropic_client: Any = None,
    dry_run: bool = False,
    now: datetime | None = None,
    since: datetime | None = None,                  # default = wiki_ledger.last_curator_b or now-3d
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

    wiki_root = lore_root / "wiki" / wiki
    if not wiki_root.exists():
        result.skipped_reasons["wiki_not_found"] = 1
        result.duration_seconds = time.monotonic() - start
        return result

    sessions_dir = wiki_root / "sessions"
    surfaces_doc = load_surfaces(wiki_root)
    if surfaces_doc is None:
        # Fall back to default standard (no SURFACES.md authored).
        surfaces_doc = load_surfaces_or_default(wiki_root)
    elif not surfaces_doc.surfaces:
        # SURFACES.md exists but parsed to zero usable surfaces — broken.
        result.skipped_reasons["surfaces_md_invalid"] = 1
        result.duration_seconds = time.monotonic() - start
        return result

    if anthropic_client is None:
        result.skipped_reasons["no_anthropic_client"] = 1
        result.duration_seconds = time.monotonic() - start
        return result

    cfg = load_wiki_config(wiki_root)

    def model_resolver(tier: str) -> str:
        return {"simple": cfg.models.simple, "middle": cfg.models.middle, "high": cfg.models.high}[tier]

    high_tier_off = cfg.models.high == "off"

    try:
        with curator_lock(lore_root, timeout=0.0):
            # Determine "recent" cutoff.
            wledger = WikiLedger(lore_root, wiki)
            wentry = wledger.read()
            cutoff = since or wentry.last_curator_b or (now - timedelta(days=3))

            notes = _load_recent_session_notes(sessions_dir, cutoff=cutoff)
            result.notes_considered = len(notes)
            if not notes:
                if not dry_run:
                    _advance_wiki_ledger(wledger, wentry, now=now)
                result.duration_seconds = time.monotonic() - start
                return result

            # Cluster.
            surface_names = [s.name for s in surfaces_doc.surfaces]
            clusters = cluster_session_notes(
                notes=notes,
                surfaces=surface_names,
                anthropic_client=anthropic_client,
                model_resolver=model_resolver,
            )
            result.clusters_formed = len(clusters)

            # Build wikilink → body map for the abstract step.
            sources_by_wl = _build_sources_map(notes)

            # Abstract each cluster, file the surfaces.
            for cluster in clusters:
                abstracted = abstract_cluster(
                    cluster=cluster,
                    surfaces_doc=surfaces_doc,
                    source_notes_by_wikilink=sources_by_wl,
                    anthropic_client=anthropic_client,
                    model_resolver=model_resolver,
                    high_tier_off=high_tier_off,
                    lore_root=lore_root,
                )
                for ab in abstracted:
                    if dry_run:
                        result.surfaces_emitted.append(
                            wiki_root / f"{ab.surface_name}s" / f"<dry-run:{_short_slug(ab.title)}>.md"
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
                    except ValueError as e:
                        # Missing required field, etc. — log + skip this one.
                        reason = "surface_filer_validation"
                        result.skipped_reasons[reason] = result.skipped_reasons.get(reason, 0) + 1

            if not dry_run:
                _advance_wiki_ledger(wledger, wentry, now=now)
    except LockContendedError:
        result.skipped_reasons["lock_contended"] = result.skipped_reasons.get("lock_contended", 0) + 1

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
    for p in sessions_dir.glob("*.md"):
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
