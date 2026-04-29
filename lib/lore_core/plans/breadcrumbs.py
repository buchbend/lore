"""Breadcrumb signals — recent commits + session wikilinks that mention plan steps.

Surfaced in the SessionStart Resume block as informational nudges
("commit abc123 references s4 — `/lore:plan-step s4 --done`?"). NEVER
mutates ``step_status``; the user/Claude does that explicitly.

Two sources scanned:

* **Git commit trailers** of the form ``Plan: <slug>#s<N>`` in the most
  recent ``n`` commits of the attached repo. Trailer-style (Git
  ``interpret-trailers`` convention) so it survives rebases (subject
  rewrites lose it; trailer rewrites preserve it).
* **Session note wikilinks** matching ``[[plan/<slug>#s<N>]]`` — the
  ``plan/`` prefix filter is essential so a same-named concept note
  with an ``s2`` heading doesn't masquerade as a plan-step reference.

The "is this a nudge?" decision is left to the renderer (SessionStart
helper). This module just returns the timestamped references.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

from lore_core.schema import extract_wikilinks, parse_frontmatter

from . import canonical

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Breadcrumb:
    """One reference to a plan step found outside the plan note itself."""

    step_id: str
    source: str  # "commit" | "session"
    ref: str  # short SHA for commits; wikilink basename for sessions
    ts: datetime  # timezone-aware (UTC); when the commit landed / note was last touched
    extra: str = ""  # commit subject for commits; description for sessions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_recent_commits(
    repo_root: Path,
    slug: str,
    *,
    n: int = 200,
    timeout_seconds: float = 5.0,
) -> list[Breadcrumb]:
    """Walk the last ``n`` commits in ``repo_root`` for ``Plan: <slug>#s<N>`` trailers.

    Returns one breadcrumb per (commit, step_id) pair, newest first.
    Multiple step references in one commit produce multiple breadcrumbs.

    Best-effort: if ``git`` isn't available, the repo is bare, or any
    subprocess error occurs, returns ``[]`` rather than raising. This
    is the SessionStart hot path; never block the banner on git
    errors.
    """
    if not repo_root or not repo_root.exists():
        return []
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                f"-n{n}",
                "--no-decorate",
                # %H short sha not needed; use %h for display
                # ISO-strict so we can parse without tz heuristics
                # %B = full commit body (subject + trailers + body)
                "--pretty=format:===%h%x09%aI%x09%s%x0a%B%x0a===END===",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return _parse_commit_log(result.stdout, slug)


def scan_recent_session_links(
    wiki_root: Path,
    slug: str,
    *,
    days: int = 14,
    sessions_dir_name: str = "sessions",
    now: datetime | None = None,
) -> list[Breadcrumb]:
    """Walk session notes from the last ``days`` for ``[[plan/<slug>#s<N>]]`` links.

    Filters by frontmatter date; falls back to mtime if frontmatter
    lacks a parseable date.
    """
    sessions_dir = wiki_root / sessions_dir_name
    if not sessions_dir.exists():
        return []
    # Aware-UTC throughout. Mixing naive `datetime.now()` (local time)
    # with frontmatter timestamps that may be UTC-aware silently shifted
    # the cutoff by the user's UTC offset. Standardize on aware-UTC at
    # every comparison.
    base_now = now if now is not None else datetime.now(UTC)
    if base_now.tzinfo is None:
        base_now = base_now.replace(tzinfo=UTC)
    cutoff = base_now - timedelta(days=days)

    target_prefix = f"plan/{slug}"
    out: list[Breadcrumb] = []
    for path in sessions_dir.rglob("*.md"):
        try:
            text = path.read_text()
            stat = path.stat()
        except OSError:
            continue
        ts = _session_timestamp(text, stat.st_mtime)
        if ts < cutoff:
            continue
        for link in extract_wikilinks(text):
            if not link.startswith(target_prefix):
                continue
            step_id = _step_id_from_link_anchor(link, target_prefix)
            if step_id is None:
                # Bare ``plan/<slug>`` mention — not a step-level breadcrumb.
                continue
            out.append(
                Breadcrumb(
                    step_id=step_id,
                    source="session",
                    ref=path.stem,
                    ts=ts,
                    extra=parse_frontmatter(text).get("description", "") or "",
                )
            )
    out.sort(key=lambda b: b.ts, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


#: Plan-trailer regex.
#:
#: Accepts both the canonical ``Plan: <slug>#step-<N>`` and the legacy
#: ``Plan: <slug>#s<N>`` form. Read-compat is permanent — historical
#: commit trailers in any vault stay actionable forever, even after
#: vaults migrate via ``lore plan migrate-ids``. Only one of the two
#: groups (canonical or legacy) is non-empty per match.
_TRAILER_RE = re.compile(
    r"^Plan:\s*([A-Za-z0-9][\w-]*)#(?:step-(\d+)|s(\d+))\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_commit_log(stdout: str, slug: str) -> list[Breadcrumb]:
    """Parse the bespoke commit-log format into Breadcrumb instances."""
    out: list[Breadcrumb] = []
    # Each commit is: "===<sha>\t<iso>\t<subject>\n<body>\n===END===\n"
    chunks = stdout.split("===END===")
    for chunk in chunks:
        chunk = chunk.lstrip("\n")
        if not chunk.startswith("==="):
            continue
        header, sep, body = chunk[3:].partition("\n")
        if not sep:
            continue
        try:
            sha, iso, subject = header.split("\t", 2)
        except ValueError:
            continue
        try:
            ts = datetime.fromisoformat(iso)
        except ValueError:
            continue
        for m in _TRAILER_RE.finditer(body):
            commit_slug = m.group(1).lower()
            if commit_slug != slug.lower():
                continue
            # Group 2 = canonical ``step-<N>``; group 3 = legacy ``s<N>``.
            # Exactly one is non-None per match. step_id is reported
            # verbatim so legacy trailers continue to match legacy
            # step_status keys until the plan is migrated.
            if m.group(2) is not None:
                step_id = f"step-{m.group(2)}"
            else:
                step_id = f"s{m.group(3)}"
            out.append(
                Breadcrumb(
                    step_id=step_id,
                    source="commit",
                    ref=sha,
                    ts=ts,
                    extra=subject,
                )
            )
    out.sort(key=lambda b: b.ts, reverse=True)
    return out


def _session_timestamp(text: str, fallback_mtime: float) -> datetime:
    """Best-effort timestamp for a session note. Always returns aware-UTC.

    Order of preference: frontmatter ``last_reviewed`` (date) →
    frontmatter ``created`` (date) → file mtime.

    Date-only fields are upgraded to noon UTC so cutoffs treat them
    as "during the day" rather than "midnight at start of day."
    """
    fm = parse_frontmatter(text)
    for key in ("last_reviewed", "created"):
        raw = fm.get(key)
        if raw is None:
            continue
        try:
            if isinstance(raw, datetime):
                return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
            if hasattr(raw, "isoformat") and not isinstance(raw, str):
                # date-typed via PyYAML
                return datetime.combine(
                    raw, datetime.min.time(), tzinfo=UTC
                ).replace(hour=12)
            parsed = datetime.fromisoformat(str(raw))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            continue
    return datetime.fromtimestamp(fallback_mtime, tz=UTC)


def _step_id_from_link_anchor(link: str, target_prefix: str) -> str | None:
    """Extract ``step-N`` or legacy ``sN`` from ``plan/<slug>#anchor``.

    Returns None if no anchor or the anchor doesn't match either step
    ID shape. Anchors are returned verbatim so legacy session-note
    wikilinks keep matching legacy step_status keys.
    """
    if "#" not in link:
        return None
    _, _, anchor = link.partition("#")
    n = canonical.parse_step_id_ordinal(anchor)
    if n is None:
        return None
    return anchor


# ---------------------------------------------------------------------------
# Render-side helpers (consumed by SessionStart in Phase 4)
# ---------------------------------------------------------------------------


def is_nudge(
    breadcrumb: Breadcrumb,
    *,
    step_status: dict[str, str],
    step_status_updated: datetime | None,
) -> bool:
    """Should this breadcrumb fire a nudge ("you may have forgotten to mark")?

    True iff: the breadcrumb's step is NOT done in step_status AND
    the breadcrumb timestamp is newer than ``step_status_updated``
    (or ``step_status_updated`` is None entirely).
    """
    current = step_status.get(breadcrumb.step_id)
    if current == "done":
        return False
    if step_status_updated is None:
        return True
    bc_ts = breadcrumb.ts
    cmp_ts = step_status_updated
    # Normalize tzinfo for comparison — both either aware or naive.
    if bc_ts.tzinfo is None and cmp_ts.tzinfo is not None:
        cmp_ts = cmp_ts.replace(tzinfo=None)
    elif bc_ts.tzinfo is not None and cmp_ts.tzinfo is None:
        bc_ts = bc_ts.replace(tzinfo=None)
    return bc_ts > cmp_ts


def newest_per_step(crumbs: Iterable[Breadcrumb]) -> dict[str, Breadcrumb]:
    """Collapse a breadcrumb stream to one entry per step (newest)."""
    out: dict[str, Breadcrumb] = {}
    for c in crumbs:
        existing = out.get(c.step_id)
        if existing is None or c.ts > existing.ts:
            out[c.step_id] = c
    return out
