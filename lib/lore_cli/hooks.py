"""Claude Code hook helpers — cheap, deterministic context injection.

These commands read cached files the linter regenerates (_index.txt,
_catalog.json) and emit bounded context blobs for the hook stream.
No LLM invocation; the only network calls are the parallel-fanned
``gh`` queries below.

Measured cost on a populated single-wiki vault (issue #27 re-audit
2026-04-28, after the gh-parallelization fix):

  - ``lore --help``                — ~600ms (Python startup + typer
                                    dispatch + eager import of ~30
                                    cmd modules in `__main__.py`)
  - ``lore hook session-start``     — ~2.0s end-to-end (the 600ms
                                    startup + ~max(issue_gh, pr_gh)
                                    parallel fetch ~1.7-2.0s + small
                                    file I/O)

Before this fix the gh fetches were sequential (issues then PRs then
each sibling), summing to ~3.7s. They now fan out via
``_run_gh_parallel`` so wall time tracks the slowest single call.
Lazy-mounting subcommand typer apps in ``__main__.py`` would cut a
further ~300-400ms but is a structural refactor.

    lore hook session-start [--cwd PATH]
    lore hook pre-compact  [--cwd PATH]
    lore hook stop

Exposed via `lore_cli.__main__` dispatch (see subcommand wiring there).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from lore_core import gh as _gh_mod
from lore_core.config import get_lore_root, get_wiki_root


def _lore_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("lore")
    except PackageNotFoundError:
        return "?"
from lore_core.git import current_repo
from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter
from lore_core.scopes import (
    load_scopes_yml,
    subtree_siblings,
    walk_scope_leaves,
)



# SessionStart writes its injected context to a cache file so /lore:context
# can show it back to the user. Two concurrent Claude sessions would
# stomp on a single shared file, so the cache is keyed by the Claude
# Code process PID — stable for the life of a session, unique across
# concurrent sessions on the same machine. The `why` subcommand
# resolves the right file by walking its own process ancestry.
def _cache_dir() -> Path:
    return Path(os.environ.get("LORE_CACHE", str(Path.home() / ".cache" / "lore")))


def _sessions_cache_dir() -> Path:
    return _cache_dir() / "sessions"


def _cache_path_for_pid(pid: int) -> Path:
    return _sessions_cache_dir() / f"{pid}.md"


def _legacy_cache_path() -> Path:
    """Pre-PID-keying cache path; read-only fallback.

    .. deprecated:: 0.10.5
       0.10.5 removed the writer. The reader fallback in ``_context_log``
       remains for one release so an upgraded environment can still
       surface a stale legacy cache instead of an empty page; that
       fallback is scheduled for removal in 0.11.0.
    """
    return _cache_dir() / "last-session-start.md"


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID exists on the current host.

    Cross-platform via ``os.kill(pid, 0)`` — the kernel performs the
    existence check without delivering a signal.

    POSIX semantics:
      - ``ProcessLookupError`` (ESRCH) → no such process → False
      - ``PermissionError`` (EPERM)    → process exists but owned by
        another user; we still know it's alive → True
      - any other ``OSError`` → conservative True (don't GC a cache
        we can't probe)
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _proc_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode(errors="replace")
    except OSError:
        return ""


def _claude_code_pid() -> int | None:
    """Walk process ancestry to find the Claude Code process PID.

    Works from any descendant (the hook process, or `lore hook why`
    invoked via the Bash tool). Returns None if /proc is unavailable or
    no Claude Code ancestor is found.

    Identification is layered because Claude Code presents itself
    differently depending on how it was launched:
      - `/proc/<pid>/exe` resolves to `CLAUDE_CODE_EXECPATH`
        (e.g. `/home/u/.local/share/claude/versions/2.1.112`) for the
        real process — this is the most reliable signal.
      - cmdline may be just `claude` (when launched via the shim
        script) or include the version path (when launched directly),
        so we check for both.
    """
    if not Path("/proc").is_dir():
        return None
    execpath = os.environ.get("CLAUDE_CODE_EXECPATH", "")
    pid = os.getpid()
    for _ in range(20):  # bounded walk — pathological cycles shouldn't loop us
        try:
            with open(f"/proc/{pid}/status") as fh:
                ppid = None
                for line in fh:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
            if not ppid or ppid <= 1:
                return None
        except OSError:
            return None
        # Most reliable: exe symlink matches the Claude Code install dir
        if execpath:
            try:
                if os.readlink(f"/proc/{ppid}/exe") == execpath:
                    return ppid
            except OSError:
                pass
        cmdline = _proc_cmdline(ppid).strip()
        # Cmdline may be the bare shim ("claude") or include the
        # version path. The bare "claude" match is deliberately exact
        # (== "claude") to avoid matching unrelated processes that
        # happen to contain the substring.
        if cmdline.rstrip() == "claude":
            return ppid
        if execpath and execpath in cmdline:
            return ppid
        if "claude-code" in cmdline or "/claude/versions/" in cmdline:
            return ppid
        pid = ppid
    return None


def _gc_sessions_cache(max_age_days: int = 14) -> None:
    """Remove stale per-PID cache files.

    A file is stale if its PID is no longer running, or (as a safety
    net on non-Linux systems where we can't check PIDs) if it's older
    than `max_age_days`. Best-effort — failures are swallowed so GC
    never breaks the hook.
    """
    sessions_dir = _sessions_cache_dir()
    if not sessions_dir.is_dir():
        return
    from time import time as _now

    cutoff = _now() - max_age_days * 86400
    for entry in sessions_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".md":
            continue
        try:
            pid = int(entry.stem)
        except ValueError:
            continue
        try:
            stale_by_age = entry.stat().st_mtime < cutoff
        except OSError:
            continue
        if _pid_alive(pid) and not stale_by_age:
            continue
        try:
            entry.unlink()
        except OSError:
            pass

# Keep auto-injected context bounded. Phase 6 expands the budget so a
# short project orientation (AGENTS.md-flavor) can ride alongside the
# banner. Per-orientation cap is ``ORIENTATION_BUDGET_CHARS``; total
# context cap stays small enough to not derail token-economy.
MAX_CONTEXT_CHARS = 5000
ORIENTATION_BUDGET_CHARS = 3000
RECENT_SESSION_DAYS = 14
MAX_OPEN_ITEMS_INLINE = 5

# Active gather-incentive directive. Inserted near the top of every
# SessionStart additionalContext block and re-asserted in PreCompact so
# the rule survives compaction. Bullet form, negatively framed — both
# stick harder in long sessions than passive permission.
#
# The canonical content lives in `lore_core/templates/integration-rules/default.md`
# (shipped as package data) so the same source feeds both this hook
# (Claude Code) and the Cursor installer's `~/.cursor/rules/lore.md`.
# Module-level `__getattr__` below preserves the historical
# `LORE_DIRECTIVE_LINES` name without reading the template at import time
# (so pytest can monkeypatch the template path without import-order pain).
def _resolve_directive_path() -> Path:
    import lore_core
    return (
        Path(lore_core.__file__).resolve().parent
        / "templates"
        / "integration-rules"
        / "default.md"
    )


_DIRECTIVE_PATH = _resolve_directive_path()


def _load_directive_lines() -> list[str]:
    """Read the canonical vault-first directive and return as a list.

    Output shape preserves the historical 3-element layout exactly:
    `["## Directives", "- **Vault first.** …", ""]`. The trailing
    empty string produces the blank line spacer in the joined output.

    Returns an empty list if the bundled template can't be read — the
    surrounding banner survives without the postscript, and the
    SessionStart top-level shield surfaces a fix hint when the root
    cause is a stale install (templates not bundled in the wheel).
    """
    try:
        text = _DIRECTIVE_PATH.read_text()
    except OSError:
        return []
    return [*text.rstrip("\n").split("\n"), ""]


def __getattr__(name: str):
    """Backwards-compat shim — keep `from hooks import LORE_DIRECTIVE_LINES`."""
    if name == "LORE_DIRECTIVE_LINES":
        return _load_directive_lines()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


PRECOMPACT_DIRECTIVE = (
    "lore: vault-first — call `lore_search` MCP before asking the user "
    "about wikilinked terms."
)

# Lines we never promote to the SessionStart open-items list — they're
# either explicitly marked ephemeral, checked off, or too trivial to
# surface every session.
EPHEMERAL_MARKERS = (
    "(ephemeral)",
    "(trivial)",
    "(todo)",
    "(skip)",
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _wiki_catalog(wiki_path: Path) -> dict | None:
    """Load _catalog.json for a wiki, or None if missing."""
    catalog_path = wiki_path / "_catalog.json"
    if not catalog_path.exists():
        return None
    try:
        return json.loads(catalog_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _wiki_hints(wiki: Path) -> dict:
    """Load `.lore-hints.yml` from a wiki root, if present.

    Schema:
        repos: [org/name, org/name2]    # repos this wiki covers
        aliases: {upstream/name: canonical/name}  # fork/mirror fixups

    Config file is user-maintained; kept out of note frontmatter so
    declaring repo coverage doesn't require touching every note.
    """
    hints_path = wiki / ".lore-hints.yml"
    if not hints_path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(hints_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _wiki_for_repo(repo: str) -> Path | None:
    """Find the wiki most relevant to the given `org/name` repo.

    Resolution order:
      1. Note-level `repos:` entries in the wiki's catalog (future-proof,
         populated by the session/curator skills as you work)
      2. Tag strings containing the repo (legacy fallback)
      3. Wiki's `.lore-hints.yml` `repos:` list (explicit coverage)
      4. Wiki name as substring of the repo's final path segment
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return None

    repo_tail = repo.rsplit("/", 1)[-1].lower()
    best_by_repos: tuple[int, Path] | None = None
    best_by_tag: tuple[int, Path] | None = None
    hints_match: Path | None = None
    name_match: Path | None = None

    for wiki in sorted(wiki_root.iterdir()):
        if not wiki.resolve().is_dir():
            continue
        wiki_name = wiki.name.lower()

        hints = _wiki_hints(wiki)
        if hints_match is None and repo in (hints.get("repos") or []):
            hints_match = wiki

        if name_match is None and wiki_name in repo_tail:
            name_match = wiki

        catalog = _wiki_catalog(wiki)
        if catalog is None:
            continue
        repo_count = 0
        tag_count = 0
        for entries in catalog.get("sections", {}).values():
            for entry in entries:
                repos = entry.get("repos") or []
                if repo in repos:
                    repo_count += 1
                tags = entry.get("tags") or []
                for tag in tags:
                    if repo in tag:
                        tag_count += 1
                        break
        if repo_count and (best_by_repos is None or repo_count > best_by_repos[0]):
            best_by_repos = (repo_count, wiki)
        if tag_count and (best_by_tag is None or tag_count > best_by_tag[0]):
            best_by_tag = (tag_count, wiki)

    if best_by_repos:
        return best_by_repos[1]
    if best_by_tag:
        return best_by_tag[1]
    if hints_match:
        return hints_match
    return name_match


def _read_wiki_index(wiki: Path, max_chars: int) -> str:
    """Return the wiki's _index.txt, truncated to fit."""
    index_path = wiki / "_index.txt"
    if not index_path.exists():
        return ""
    text = index_path.read_text(errors="replace")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + "\n... (truncated — run /lore:context for full)"


# Matches the loose-ends-style section up to next `##` or EOF.
#
# Recognises three heading shapes for back-compat across the v1/v2/v3
# session-note revisions:
#   - ``## Open items``   (v1, pre-revision; never auto-flipped to v2)
#   - ``## Loose ends``   (v2 + v3, current)
# ``## Issues touched`` (v2-only — actual gh issue references) is
# deliberately NOT matched here: its content is "things filed
# elsewhere", not "discussed but not pursued".
_OPEN_ITEMS_RE = re.compile(
    r"##\s+(?:Open items|Loose ends)\s*\n(.+?)(?=\n##|\Z)",
    re.DOTALL,
)


def _session_touches_repo(text: str, fm: dict, repo: str) -> bool:
    """Return True if a session note concerns the given repo.

    Order of evidence:
      1. Session frontmatter `repos:` includes the repo
      2. Session body literally mentions `<repo>` or its tail (`name`)
    """
    repos = fm.get("repos") or []
    if repo in repos:
        return True
    tail = repo.rsplit("/", 1)[-1]
    # Cheap substring check — false positives are tolerable here
    return repo in text or (tail and tail in text)


def _is_ephemeral(item: str) -> bool:
    lower = item.lower()
    return any(marker in lower for marker in EPHEMERAL_MARKERS)


def _session_note_date(path: Path) -> date | None:
    """Infer the work-date for a session note from its sharded path.

    Two layouts in the wild:

    - Sharded (current): ``sessions/.../YYYY/MM/DD-slug.md`` — year/month
      come from the parent dirs, day from the filename's leading
      ``DD-`` prefix.
    - Flat-legacy: ``sessions/YYYY-MM-DD-slug.md`` — the full date
      sits in the filename.

    Returns ``None`` when neither shape matches (the file isn't a
    real session note — e.g. an inbox draft or stray markdown).
    """
    name = path.name
    # Flat-legacy: filename starts with YYYY-MM-DD.
    if len(name) >= 10 and name[4] == "-" and name[7] == "-":
        try:
            return date.fromisoformat(name[:10])
        except ValueError:
            pass
    # Sharded: YYYY/MM/ parent dirs + DD-... filename.
    if len(name) >= 3 and name[2] == "-":
        try:
            day = int(name[:2])
            month = int(path.parent.name)
            year = int(path.parent.parent.name)
            return date(year, month, day)
        except (ValueError, IndexError):
            pass
    return None


def _last_session_hint(wiki: Path, max_notes: int = 2) -> list[tuple[str, str]]:
    """Return (slug, status-line text) pairs for the most recent session notes.

    The session layout is sharded ``sessions/<YYYY>/<MM>/<DD>-<slug>.md``
    (and optionally team-mode-handle-shard
    ``sessions/<handle>/<YYYY>/<MM>/<DD>-<slug>.md``), so we walk the
    whole subtree with ``rglob`` and filter to date-prefixed filenames.

    Status-line preference:

    1. ``title`` — content-named, short. The session-note revision adds
       this explicitly for SessionStart's status-line consumption.
    2. ``description`` — short paragraph (revision form).
    3. ``summary`` — back-compat for legacy notes that pre-date the
       revision (where ``summary`` was the paragraph and ``description``
       was the short headline).

    Does not filter by user — any user's sessions are shown for
    cross-user awareness.

    The previous implementation read only the first 1024 bytes — on
    real-world notes the ``source_transcripts`` block (with full
    SHA-256 hashes) plus a long ``summary`` paragraph blew past that
    cap, so the status-line silently went empty. Cap removed; we read
    each candidate fully and let ``parse_frontmatter`` stop at the
    closing ``---``.
    """
    from lore_core.schema import parse_frontmatter
    from lore_core.session_writer import session_path_sort_key

    sessions_dir = wiki / "sessions"
    if not sessions_dir.is_dir():
        return []

    # Sort newest-first using the layout-aware sort key — handles both
    # the new ``DD-HHMM-slug.md`` shape and legacy ``DD-slug.md`` (the
    # latter sinks to the bottom of its day; see helper docstring).
    candidates = sorted(
        (p for p in sessions_dir.rglob("*.md") if p.is_file() and not p.name.startswith("_")),
        key=session_path_sort_key,
        reverse=True,
    )
    results: list[tuple[str, str]] = []
    for path in candidates:
        if len(results) >= max_notes:
            break
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if fm.get("type") != "session":
            continue
        # title (revision) → description (revision short form) → summary
        # (legacy paragraph) — all three are valid status-line content.
        hint = fm.get("title") or fm.get("description") or fm.get("summary")
        if not hint:
            continue
        results.append((path.stem, hint))
    return results


def _cross_scope_breadcrumbs(lore_root: Path, current_wiki: str) -> list[str]:
    """One-liner per other-wiki with activity in the last 24h."""
    from collections import Counter
    from datetime import UTC, datetime, timedelta

    from lore_core.drain import SYSTEM_SESSION, DrainStore

    store = DrainStore(lore_root, SYSTEM_SESSION)
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    events = store.read(since=cutoff, limit=500)
    if not events:
        return []
    wiki_counts: Counter[str] = Counter()
    for e in events:
        if e.wiki and e.wiki != current_wiki:
            wiki_counts[e.wiki] += 1
    lines: list[str] = []
    for wiki_name, count in wiki_counts.most_common():
        noun = "event" if count == 1 else "events"
        lines.append(f"Also today: {count} {noun} in {wiki_name}")
    return lines


def _recent_open_items(
    wiki: Path,
    repo: str | None = None,
    days: int = RECENT_SESSION_DAYS,
) -> tuple[list[str], int]:
    """Parse `## Open items` from recent session notes.

    When `repo` is given, only sessions that touch that repo contribute
    items to the primary list; items from other sessions are counted
    as "elsewhere in the wiki" so the caller can show a collapsed
    pointer rather than a dump.

    Returns (items_for_repo, count_elsewhere).
    """
    sessions_dir = wiki / "sessions"
    if not sessions_dir.is_dir():
        return [], 0
    cutoff = date.today() - timedelta(days=days)
    items: list[str] = []
    seen: set[str] = set()
    elsewhere = 0

    # Sharded layout: walk recursively. The previous flat
    # ``sessions_dir.glob("*.md")`` only ever found ``_recent.md`` (a
    # cached pointer file), so the loose-ends harvest was empty in
    # production. Date filter accepts both YYYY-MM-DD-prefixed (legacy
    # team-mode flat) and the DD-prefixed names that live under
    # ``<year>/<month>/`` — for the latter we compare against the
    # parent month directory for the year/month and the filename's DD
    # prefix.
    for md in sorted(sessions_dir.rglob("*.md"), reverse=True):
        if not md.is_file() or md.name.startswith("_"):
            continue
        d = _session_note_date(md)
        if d is None:
            continue
        if d < cutoff:
            continue
        text = md.read_text(errors="replace")
        fm = parse_frontmatter(text)
        m = _OPEN_ITEMS_RE.search(text)
        if not m:
            continue
        matches_repo = True if repo is None else _session_touches_repo(text, fm, repo)
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line.startswith("-"):
                continue
            body = line.lstrip("-").strip()
            if not body or body.lower() == "none":
                continue
            if _is_ephemeral(body):
                continue
            if body in seen:
                continue
            seen.add(body)
            if matches_repo:
                items.append(body)
            else:
                elsewhere += 1
    return items, elsewhere


def _project_note_for_repo(wiki: Path, repo: str) -> dict | None:
    """Find a project note whose filename or frontmatter matches the repo.

    Returns a dict with {name, description, path} or None.
    """
    catalog_path = wiki / "_catalog.json"
    if not catalog_path.exists():
        return None
    try:
        catalog = json.loads(catalog_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    tail = repo.rsplit("/", 1)[-1].lower()
    projects = catalog.get("sections", {}).get("projects", [])
    # Prefer exact repo match in frontmatter
    for entry in projects:
        repos = entry.get("repos") or []
        if repo in repos:
            return entry
    # Fall back to filename match
    for entry in projects:
        name = (entry.get("name") or "").lower()
        if name == tail or name.replace("-", "") == tail.replace("-", ""):
            return entry
    return None


def _stale_count(wiki: Path) -> int:
    """Count notes with `status: stale` per the catalog."""
    catalog = _wiki_catalog(wiki)
    if not catalog:
        return 0
    count = 0
    for entries in catalog.get("sections", {}).values():
        for entry in entries:
            if entry.get("status") == "stale":
                count += 1
    return count


# ---------------------------------------------------------------------------
# Scope + gh integration (schema v2 — superseded `## Open items` scraping
# when the cwd's CLAUDE.md has a `## Lore` section)
# ---------------------------------------------------------------------------


GH_TIMEOUT_SECONDS = 10
MAX_ISSUES_INLINE = 5
MAX_PRS_INLINE = 3

# Ancestor-walk for ## Lore is canonical in lore_core.session. Imported
# lazily below at call sites to avoid a module-load-order wobble.


# Scope helpers now live in `lore_core.scopes` so the `lore resume` CLI
# can share them. Local underscore-prefixed delegates kept so tests that
# monkeypatch these names against the `hooks` module continue to work.
_load_scopes_yml = load_scopes_yml
_walk_scope_leaves = walk_scope_leaves
_subtree_siblings = subtree_siblings


# gh wrappers moved to `lore_core.gh`. The underscore-prefixed names are
# kept as thin delegates so tests that monkeypatch `hooks._run_gh` still
# intercept every call made from this module.


def _run_gh(kind: str, repo: str, filter_args: list[str]) -> list[dict]:
    """Indirection kept for the test-monkeypatch contract. ``test_hooks_v2``
    swaps this to feed deterministic JSON. Do NOT inline."""
    return _gh_mod.run_gh(kind, repo, filter_args)


def _run_gh_parallel(
    calls: list[tuple[str, str, list[str]]],
) -> list[list[dict]]:
    """Fan out ``_run_gh`` calls concurrently; preserve input order.

    Each ``calls`` entry is ``(kind, repo, filter_args)`` matching
    ``_run_gh``. SessionStart used to pay these gh fetches serially
    (~1.7s each); fanning them out keeps wall time at the slowest
    single call. Routes through ``_run_gh`` so the
    ``hooks._run_gh`` monkeypatch in ``test_hooks_v2`` still
    intercepts every fetch.
    """
    if not calls:
        return []
    if len(calls) == 1:
        return [_run_gh(*calls[0])]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(calls))) as pool:
        futures = [pool.submit(_run_gh, *c) for c in calls]
        return [f.result() for f in futures]


# ---------------------------------------------------------------------------
# Session-start hook
# ---------------------------------------------------------------------------


_PLAN_INLINE_CAP = 3


def _active_plans_resume_block(
    wiki: Path, repo: str | None, *, repo_root: Path | None = None
) -> tuple[list[str], int]:
    """Build the SessionStart Resume block lines for the active plans.

    Returns ``(lines, plan_count)``. ``lines`` is empty when no plans
    qualify; ``plan_count`` feeds the status-line summary ("1 plan",
    "3 plans") regardless of whether the inline block was rendered.

    Reads ``glob('<wiki>/plans/*.md')`` directly via the registry —
    NEVER ``_catalog.json`` — so the headline demo path
    (``accept → /clear → restart``, no lint between) shows the just-
    captured plan immediately.

    Defensive: any exception inside is swallowed so SessionStart never
    crashes on a malformed plan note.
    """
    try:
        from lore_core.plans.breadcrumbs import (
            is_nudge,
            newest_per_step,
            scan_recent_commits,
            scan_recent_session_links,
        )
        from lore_core.plans.registry import list_active

        cards = list_active(wiki, repo=repo)
        if not cards:
            return [], 0

        lines: list[str] = []
        rendered = 0
        for card in cards:
            if rendered >= _PLAN_INLINE_CAP:
                break
            # First card uses ## Resume:; subsequent cards demote to
            # ### so the rendered output reads as ONE Resume section
            # with multiple plans rather than three peer headings.
            heading_level = 2 if rendered == 0 else 3
            lines.extend(
                _render_one_plan_card(
                    card,
                    repo_root=repo_root,
                    scan_commits=scan_recent_commits,
                    scan_sessions=scan_recent_session_links,
                    wiki_root=wiki,
                    is_nudge_fn=is_nudge,
                    newest_per_step_fn=newest_per_step,
                    heading_level=heading_level,
                )
            )
            lines.append("")  # blank between cards
            rendered += 1

        # Trim trailing blank.
        while lines and lines[-1] == "":
            lines.pop()

        more = len(cards) - rendered
        if more > 0:
            extra_slug = cards[rendered].slug
            lines.append("")
            lines.append(
                f"+{more} more active plan{'s' if more != 1 else ''} — "
                f"`/lore:plan-resume {extra_slug}` to expand"
            )

        return lines, len(cards)
    except Exception:  # noqa: BLE001 — never break SessionStart
        return [], 0


def _render_one_plan_card(
    card,
    *,
    repo_root: Path | None,
    scan_commits,
    scan_sessions,
    wiki_root: Path,
    is_nudge_fn,
    newest_per_step_fn,
    heading_level: int = 2,
) -> list[str]:
    """Render one plan as a Resume-block card.

    ``heading_level`` controls the heading depth: 2 for the first card
    (``## Resume: <title> …``), 3 for subsequent ones (``### <title> …``)
    so a multi-plan Resume block reads as one section, not N peers.

    Card shape per :class:`lore_core.plans.registry.ActivePlanCard`.
    """
    title = card.description or card.slug
    total = card.steps_total
    done = card.steps_done
    in_prog = card.steps_in_progress
    blocked = card.steps_blocked
    next_pending = card.next_pending_step()

    summary_bits = [f"{done}/{total} done"]
    if in_prog:
        summary_bits.append(f"{len(in_prog)} in-progress")
    if blocked:
        summary_bits.append(f"{len(blocked)} blocked")
    summary_bits.append(_stale_marker(card))
    summary_bits = [b for b in summary_bits if b]
    summary = " · ".join(summary_bits)
    if heading_level == 2:
        header = f"## Resume: {title} · {summary}"
    else:
        # Subsequent cards: drop the verb (it's already in the H2),
        # lead with the title at one heading level deeper.
        header = f"### {title} · {summary}"

    body: list[str] = [header]

    # Body lines — what's actively going on
    if in_prog:
        body.append(
            f"In progress: {' · '.join(_step_label(card, sid) for sid in in_prog)}"
        )
    if next_pending:
        body.append(f"Next pending: {_step_label(card, next_pending)}")
    elif done == total and total > 0:
        body.append("All steps done — `/lore:plan-advance " + card.slug + " --complete`?")

    # Wikilink line so the user can copy-paste into a session note
    anchor = in_prog[0] if in_prog else next_pending
    if anchor:
        body.append(f"[[plan/{card.slug}#{anchor}]]")
        # Trailer demoted to override (v0.35+). Auto-attribution rides
        # `step_files`: PostToolUse:Edit flips pending → in_progress on
        # first matching edit; Stop's LLM judgment closes the step on
        # high-confidence commit overlap. The trailer is the explicit
        # short-circuit and `/lore:plan-step --done` is the manual one.
        body.append(
            f"Step status: edits → in_progress; commits → LLM-judged. "
            f"Override: `Plan: {card.slug}#{anchor}` trailer or "
            f"`/lore:plan-step {card.slug} --done`."
        )
    else:
        body.append(f"[[plan/{card.slug}]]")

    # Breadcrumb nudges
    body.extend(
        _render_breadcrumb_nudges(
            card,
            repo_root=repo_root,
            wiki_root=wiki_root,
            scan_commits=scan_commits,
            scan_sessions=scan_sessions,
            is_nudge_fn=is_nudge_fn,
            newest_per_step_fn=newest_per_step_fn,
        )
    )
    return body


def _step_label(card, step_id: str) -> str:
    """Format ``s2 — Implement new login flow``. Title is best-effort."""
    title = _read_step_title(card.path, step_id)
    if title:
        return f"{step_id} — {title}"
    return step_id


def _read_step_title(plan_path: Path, step_id: str) -> str:
    """Pull the human title out of ``### s<N>: <title>``. Best-effort."""
    try:
        text = plan_path.read_text()
    except OSError:
        return ""
    pattern = re.compile(rf"^###\s+{re.escape(step_id)}:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _stale_marker(card) -> str:
    """``stale (Nd)`` if the plan hasn't been reviewed in >7 days, else ``""``.

    Uses UTC date to match what the writer side persists — local-date
    comparison silently flips the marker on/off across DST boundaries
    and the IDL.
    """
    try:
        from datetime import UTC as _UTC
        from datetime import date as _date
        from datetime import datetime as _dt

        if not card.last_reviewed:
            return ""
        last = _date.fromisoformat(card.last_reviewed)
        today_utc = _dt.now(_UTC).date()
        days = (today_utc - last).days
        if days > 7:
            return f"stale ({days}d)"
    except (ValueError, TypeError):
        pass
    return ""


def _render_breadcrumb_nudges(
    card,
    *,
    repo_root: Path | None,
    wiki_root: Path,
    scan_commits,
    scan_sessions,
    is_nudge_fn,
    newest_per_step_fn,
) -> list[str]:
    """Surface ⚠ nudges for commit/session refs ahead of step_status."""
    from datetime import datetime as _dt
    crumbs = []
    if repo_root is not None:
        crumbs.extend(scan_commits(repo_root, card.slug, n=200))
    crumbs.extend(scan_sessions(wiki_root, card.slug, days=14))
    if not crumbs:
        return []
    newest = newest_per_step_fn(crumbs)

    # Parse step_status_updated for the is_nudge comparison.
    step_status_updated_dt: _dt | None = None
    if card.step_status_updated:
        try:
            from datetime import UTC as _UTC
            parsed = _dt.fromisoformat(card.step_status_updated.replace("Z", "+00:00"))
            step_status_updated_dt = (
                parsed if parsed.tzinfo else parsed.replace(tzinfo=_UTC)
            )
        except (ValueError, TypeError):
            step_status_updated_dt = None

    nudges: list[str] = []
    for step_id in sorted(newest):
        crumb = newest[step_id]
        if not is_nudge_fn(
            crumb,
            step_status=card.step_status,
            step_status_updated=step_status_updated_dt,
        ):
            continue
        if crumb.source == "commit":
            nudges.append(
                f"- ⚠ commit {crumb.ref} references {crumb.step_id} — "
                f"`/lore:plan-step {crumb.step_id} --done`?"
            )
        else:
            nudges.append(
                f"- ⚠ session [[{crumb.ref}]] links {crumb.step_id} — "
                f"`/lore:plan-step {crumb.step_id} --done`?"
            )
    if not nudges:
        return []
    # Lead with a blank line so the nudges start a new paragraph rather
    # than collapsing into the wikilink line as a continuation.
    return ["", *nudges]


def _session_start_from_lore(
    cwd: str,
    config: tuple[Path, dict],
    wiki_root: Path,
) -> str | None:
    """Build SessionStart output from a `## Lore` config block.

    Returns the formatted output, or None if the config is unusable
    (wiki doesn't exist) so the caller falls through to the legacy
    path. `gh` failures never raise — they just result in empty lists.
    """
    _, block = config
    wiki_name = block.get("wiki")
    scope = block.get("scope") or ""
    backend = block.get("backend") or "github"
    issues_filter = block.get("issues") or "--assignee @me --state open"
    prs_filter = block.get("prs") or "--author @me"

    if not wiki_name:
        return None
    wiki = wiki_root / wiki_name
    if not wiki.exists():
        return None

    repo = current_repo(cwd)

    issues: list[dict] = []
    prs: list[dict] = []
    subtree_issues = 0
    subtree_scope = ""

    if backend == "github" and repo:
        issues_args = _gh_mod.split_filter(issues_filter)
        prs_args = _gh_mod.split_filter(prs_filter)
        calls: list[tuple[str, str, list[str]]] = [
            ("issue", repo, issues_args),
            ("pr", repo, prs_args),
        ]
        if scope:
            scopes = _load_scopes_yml(wiki)
            siblings = _subtree_siblings(scopes, scope)
            parts = scope.split(":")
            subtree_scope = ":".join(parts[:-1]) if len(parts) > 1 else ""
            for _sib_scope, sib_repo in siblings:
                if sib_repo == repo:
                    continue
                calls.append(("issue", sib_repo, issues_args))

        results = _run_gh_parallel(calls)
        issues = results[0]
        prs = results[1]
        for sib_result in results[2:]:
            subtree_issues += len(sib_result)

    project_entry = _project_note_for_repo(wiki, repo) if repo else None
    session_hints = _last_session_hint(wiki)
    plan_resume_block, plan_count = _active_plans_resume_block(
        wiki, repo, repo_root=Path(cwd)
    )

    injected_bits: list[str] = []
    if plan_count == 1:
        injected_bits.append("1 plan")
    elif plan_count > 1:
        # Append "(K shown)" only when the cap kicked in, so the status
        # line never lies about what the user is actually looking at.
        if plan_count > _PLAN_INLINE_CAP:
            injected_bits.append(f"{plan_count} plans ({_PLAN_INLINE_CAP} shown)")
        else:
            injected_bits.append(f"{plan_count} plans")
    # Prefer scope (the routing identity the user typed at attach time)
    # over the project-note wikilink — `ccat:ops-db-api-client` tells the
    # user where they are in the scope tree; `[[ops-db-api-client]]` only
    # tells them a project note exists. Fall back to the wikilink when
    # scope is empty (legacy attachments, edge cases).
    if scope:
        injected_bits.append(scope)
    elif project_entry is not None:
        injected_bits.append(f"[[{project_entry['name']}]]")
    if session_hints:
        _, first_summary = session_hints[0]
        extra = f" +{len(session_hints) - 1}" if len(session_hints) > 1 else ""
        injected_bits.append(f"last note: {first_summary}{extra}")
    if issues:
        injected_bits.append(f"{len(issues)} issue{'s' if len(issues) != 1 else ''}")
    if prs:
        injected_bits.append(f"{len(prs)} PR{'s' if len(prs) != 1 else ''}")
    status_line = f"lore {_lore_version()}: active" + (" · " + " · ".join(injected_bits) if injected_bits else "")

    out_parts: list[str] = [status_line, ""]
    # Resume block comes FIRST after the status line — it's the highest-value,
    # most-actionable item for the headline zero-handover demo. UX delta from
    # the design pivot: lead with the verb ("Resume:"), human title, then
    # wikilink + step anchor on its own line.
    if plan_resume_block:
        out_parts.extend(plan_resume_block)
        out_parts.append("")
    # Pending-attribution bridge: surface low-confidence / skip / no-LLM
    # cases parked by Stop in prior sessions for any currently-active plan
    # in this repo. Closes the loop the user-terminal nudge couldn't.
    pending_block = _pending_attributions_block(wiki, repo=repo)
    if pending_block:
        out_parts.extend(pending_block)
        out_parts.append("")
    if project_entry is not None:
        out_parts.append(f"## Focus: [[{project_entry['name']}]]")
        desc = project_entry.get("description")
        if desc:
            out_parts.append(desc)
        children = project_entry.get("children") or []
        if children:
            link_list = ", ".join(f"[[{c}]]" for c in children[:6])
            more = f" +{len(children) - 6}" if len(children) > 6 else ""
            out_parts.append(f"Linked notes: {link_list}{more}")
        out_parts.append("")
    elif repo:
        out_parts.append(f"_Repo `{repo}` has no dedicated project note in {wiki_name}._")
        out_parts.append("")

    if session_hints:
        for slug, desc in session_hints:
            out_parts.append(f"Last: [[{slug}]] — {desc}")
        out_parts.append("")

    if issues:
        header = f"## Open issues ({scope})" if scope else "## Open issues"
        out_parts.append(header)
        for issue in issues[:MAX_ISSUES_INLINE]:
            out_parts.append(_gh_mod.format_issue_line(issue))
        if len(issues) > MAX_ISSUES_INLINE:
            out_parts.append(f"- … +{len(issues) - MAX_ISSUES_INLINE} more for this repo")
        out_parts.append("")
    if subtree_issues and subtree_scope:
        out_parts.append(
            f"+{subtree_issues} from `{subtree_scope}` subtree — "
            f"`/lore:resume {subtree_scope}` to expand"
        )
        out_parts.append("")
    if not issues and not subtree_issues and backend == "github":
        out_parts.append("_No open issues matched your filters._")
        out_parts.append("")

    if prs:
        out_parts.append("## Open PRs")
        for pr in prs[:MAX_PRS_INLINE]:
            out_parts.append(_gh_mod.format_pr_line(pr))
        if len(prs) > MAX_PRS_INLINE:
            out_parts.append(f"- … +{len(prs) - MAX_PRS_INLINE} more")
        out_parts.append("")

    # Directive last: status + focus + open items show what Lore did for
    # the user *first*; the rule postscript reasserts the contract without
    # competing for the most-attention slot at the top of the banner.
    out_parts.extend(_load_directive_lines())
    out_parts.extend(_citation_directive_lines())
    out_parts.extend(_journal_directive_lines())

    return "\n".join(out_parts)


def _session_start(cwd: str | None) -> str:
    """Build the SessionStart context block.

    Prefers the `## Lore`-driven path (schema v2) when the cwd resolves
    an ancestor CLAUDE.md with a `## Lore` section. Falls back to the
    legacy `## Open items` scrape for wikis without explicit attach
    configuration.
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        # Compose the hint from observable state directly — env var or
        # config-file presence — without consulting lore_root_source().
        # Source labels are debug-only; branching on them ties this
        # caller to the resolver's internal taxonomy.
        from lore_core.config import user_config_path
        env_value = os.environ.get("LORE_ROOT", "").strip()
        cfg_path = user_config_path()
        if env_value:
            hint = f"$LORE_ROOT={env_value}"
        elif cfg_path.exists():
            hint = f"{cfg_path} → {get_lore_root()}"
        else:
            hint = f"(unset, fallback {get_lore_root()})"
        return (
            f"lore: no vault at {hint}. "
            "Set $LORE_ROOT, write ~/.config/lore/config.yml, or run `lore init`."
        )

    # Schema v2 path: cwd has (or inherits) a `## Lore` section.
    if cwd:
        from lore_core.session import _resolve_attach_block
        cfg = _resolve_attach_block(Path(cwd))
        if cfg is not None:
            v2 = _session_start_from_lore(cwd, cfg, wiki_root)
            if v2 is not None:
                return v2

    # Legacy path: resolve wiki from repo, scrape `## Open items`.
    repo = current_repo(cwd)
    wiki = _wiki_for_repo(repo) if repo else None

    if wiki is None:
        wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
        if len(wikis) == 1:
            wiki = wikis[0]

    if wiki is None:
        if repo:
            return (
                f"lore: no wiki covers `{repo}`. Add it to a wiki's "
                "`.lore-hints.yml` or run `/lore:session` to auto-tag."
            )
        return f"lore: no wiki resolved in {wiki_root}."

    # Repo-scoped open items (repo or None for wiki-wide)
    items, elsewhere = _recent_open_items(wiki, repo=repo)

    # Project note focused on this repo, if any
    project_entry = _project_note_for_repo(wiki, repo) if repo else None
    session_hints = _last_session_hint(wiki)

    # Status line enumerates what's actually injected into context
    injected_bits: list[str] = []
    if project_entry is not None:
        injected_bits.append(f"[[{project_entry['name']}]]")
    if session_hints:
        _, first_summary = session_hints[0]
        extra = f" +{len(session_hints) - 1}" if len(session_hints) > 1 else ""
        injected_bits.append(f"last note: {first_summary}{extra}")
    if items:
        injected_bits.append(f"{len(items)} open item{'s' if len(items) != 1 else ''}")
    status_line = f"lore {_lore_version()}: active" + (" · " + " · ".join(injected_bits) if injected_bits else "")

    parts: list[str] = [status_line, ""]

    if project_entry is not None:
        parts.append(f"## Focus: [[{project_entry['name']}]]")
        desc = project_entry.get("description")
        if desc:
            parts.append(desc)
        children = project_entry.get("children") or []
        if children:
            link_list = ", ".join(f"[[{c}]]" for c in children[:6])
            more = f" +{len(children) - 6}" if len(children) > 6 else ""
            parts.append(f"Linked notes: {link_list}{more}")
        parts.append("")
    elif repo:
        parts.append(f"_Repo `{repo}` has no dedicated project note in {wiki.name}._")
        parts.append("")

    if session_hints:
        for slug, desc in session_hints:
            parts.append(f"Last: [[{slug}]] — {desc}")
        parts.append("")

    if items:
        # Loose-ends framing: things discussed but not pursued — NOT a
        # TODO list. Surviving work belongs in the configured PM
        # backend (gh issues / Jira / etc.), not here.
        parts.append(f"## Loose ends from recent sessions{' (this repo)' if repo else ''}")
        for item in items[:MAX_OPEN_ITEMS_INLINE]:
            parts.append(f"- {item}")
        extras: list[str] = []
        if len(items) > MAX_OPEN_ITEMS_INLINE:
            extras.append(f"+{len(items) - MAX_OPEN_ITEMS_INLINE} more for this repo")
        if elsewhere:
            extras.append(f"+{elsewhere} elsewhere in {wiki.name}")
        if extras:
            parts.append(f"- … ({'; '.join(extras)}; `/lore:resume` to expand)")
        parts.append("")
    elif elsewhere:
        parts.append(
            f"No loose ends from recent sessions for this repo. "
            f"{elsewhere} elsewhere in {wiki.name} — `/lore:resume` to see."
        )
        parts.append("")

    # Directive last: see _session_start_from_lore for rationale.
    parts.extend(_load_directive_lines())
    parts.extend(_citation_directive_lines())
    parts.extend(_journal_directive_lines())

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pre-compact hook
# ---------------------------------------------------------------------------


def _pre_compact(cwd: str | None) -> str:
    """One-line hint that survives compaction.

    PreCompact emits into `systemMessage`, which is a visible banner
    to the user on every compaction — so we keep it to one short line.
    The full open-items context is already in SessionStart's
    additionalContext and stays with the agent until manually cleared.
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return ""
    repo = current_repo(cwd)
    wiki = _wiki_for_repo(repo) if repo else None
    if wiki is None:
        wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
        if len(wikis) == 1:
            wiki = wikis[0]
    if wiki is None:
        return ""

    items, _elsewhere = _recent_open_items(wiki, repo=repo)
    scope = wiki.name if repo is None else f"{wiki.name}:{repo.rsplit('/', 1)[-1]}"

    # Always re-assert the vault-first directive across compaction —
    # compliance decay is real, and the rule must survive even when no
    # open items are pending. Open-items hint is optional.
    if items:
        return (
            f"lore: {len(items)} open items for {scope} carry past compaction — "
            "run /lore:resume if the agent needs them refreshed. "
            + PRECOMPACT_DIRECTIVE
        )
    return PRECOMPACT_DIRECTIVE


# ---------------------------------------------------------------------------
# `lore hook why` — read-only cache lookup for the /lore:context skill
# ---------------------------------------------------------------------------


def _context_log() -> str:
    """Read the PID-scoped context log. Pure file read — no live I/O."""
    cc_pid = _claude_code_pid()
    if cc_pid is not None:
        path = _cache_path_for_pid(cc_pid)
        if path.exists():
            try:
                return path.read_text(errors="replace")
            except OSError:
                pass
    legacy = _legacy_cache_path()
    if legacy.exists():
        try:
            body = legacy.read_text(errors="replace")
        except OSError:
            body = ""
        if body:
            return (
                "_(showing the most recent context log — your current "
                "Claude Code session may not have written one yet)_\n\n"
                + body
            )
    return "lore: no context log found. SessionStart may not have fired. Run `lore doctor`.\n"


def _append_context_log(sys_msg: str, ctx: str | None = None) -> None:
    """Append a timestamped heartbeat entry to the PID-scoped context log."""
    from datetime import UTC as _UTC, datetime as _dt
    cc_pid = _claude_code_pid() or os.getppid()
    cache = _cache_path_for_pid(cc_pid)
    if not cache.exists():
        return
    ts = _dt.now(_UTC).strftime("%H:%M")
    entry = f"\n── {ts} ──\n{sys_msg}\n"
    if ctx:
        entry += f"  → injected: {ctx}\n"
    try:
        with open(cache, "a") as f:
            f.write(entry)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Stop hook (timeout-style prompt)
# ---------------------------------------------------------------------------


def _stop() -> str:
    """No-op — session capture is automatic."""
    return ""


def _plan_trailer_nudges_for_stop(cwd_path: Path) -> list[str]:
    """Auto-advance plan steps from `Plan: <slug>#sN` commit trailers.

    A `Plan:` trailer in a commit body is a binding promise from the
    author that this commit closed step ``sN``. The Stop hook honors
    that promise directly: it calls ``set_step(slug, sN, DONE)`` and
    emits a confirmation line so the model sees what just happened.
    Layer B in ``step_status._mutate_under_lock`` then auto-flips the
    plan's top-level ``status`` to ``done`` once the last step lands.

    Per-session seen-set at
    ``~/.cache/lore/sessions/<sid>/plan-nudges.seen`` records every
    ``<sha>#<step_id>`` key already actioned. Same-second-but-
    different commits land separate entries; identical (sha, step)
    pairs are skipped. ``set_step`` is itself idempotent (no-op fast
    path on identical writes), so the seen-set is belt-and-braces:
    keeps the confirmation line from re-firing each Stop until session
    end.

    Always best-effort: returns ``[]`` on any error so a malformed
    plan or git failure can't break Stop. Per-trailer ``set_step``
    failures are swallowed individually so one bad plan doesn't
    block the rest of the batch.
    """
    try:
        from lore_core.drain import resolve_session_id
        from lore_core.git import git_repo_root
        from lore_core.plans.breadcrumbs import scan_recent_commits
        from lore_core.plans.registry import list_active

        scope = resolve_scope(cwd_path)
        if scope is None:
            return []
        wiki_root = get_wiki_root() / scope.wiki
        if not wiki_root.exists():
            return []
        repo_slug = current_repo(cwd_path)
        repo_root = git_repo_root(cwd_path)
        if repo_root is None:
            return []

        cards = list_active(wiki_root, repo=repo_slug)
        if not cards:
            return []

        sid, _ = resolve_session_id(cwd_path)
        seen_path = (
            Path.home() / ".cache" / "lore" / "sessions" / sid / "plan-nudges.seen"
        )
        seen = _read_nudge_seen_set(seen_path)

        nudges: list[str] = []
        new_keys: list[str] = []
        for card in cards[:_PLAN_INLINE_CAP]:
            crumbs = scan_recent_commits(repo_root, card.slug, n=20)
            if not crumbs:
                continue

            # Per-step done is the eligibility check we want here. The
            # global ``step_status_updated`` timestamp gate inside
            # ``is_nudge`` was right for the old manual-advance flow
            # (where same-second commits across distinct steps couldn't
            # collide), but in the auto-advance flow the very first
            # set_step bumps step_status_updated to ``now`` — a sibling
            # commit landing in the same second would be wrongly
            # filtered. The seen-set already handles session-level
            # dedup; per-step status handles cross-session safety.
            current_status = {**(card.step_status or {})}
            for crumb in crumbs:
                key = f"{crumb.ref}#{crumb.step_id}"
                if key in seen:
                    continue
                if current_status.get(crumb.step_id) == "done":
                    continue
                # Honor the trailer: actually mark the step done. Per-
                # trailer try/except so a malformed plan or invalid
                # step_id doesn't cascade into the next plan's
                # processing.
                try:
                    from lore_core.plans.step_status import set_step
                    from lore_core.plans.types import StepStatus
                    set_step(
                        wiki_root=wiki_root,
                        slug=card.slug,
                        step_id=crumb.step_id,
                        status=StepStatus.DONE,
                    )
                    current_status[crumb.step_id] = "done"
                    nudges.append(
                        f"✓ marked plan/{card.slug}#{crumb.step_id} done "
                        f"from commit {crumb.ref}"
                    )
                    new_keys.append(key)
                except Exception:  # noqa: BLE001
                    # Don't drop the seen-key here — leaving it absent
                    # lets a future Stop retry the write if the cause
                    # was transient (e.g. lock contention).
                    continue

        if new_keys:
            _append_nudge_seen_set(seen_path, new_keys)

        return nudges
    except Exception:  # noqa: BLE001 — never break Stop
        return []


#: Plan-trailer regex used by Stop-hook nudges.
#:
#: Accepts both canonical ``step-<N>`` and legacy ``s<N>`` anchors so
#: historical commits keep matching after the rename. Match groups:
#: 1 = slug, 2 = full step ID (``step-N`` or ``sN``).
_PLAN_TRAILER_RE = re.compile(
    r"^Plan:\s*([\w./-]+)#(step-\d+|s\d+)\s*$", re.IGNORECASE | re.MULTILINE
)

def _commit_files(repo_root: Path, sha: str) -> set[str]:
    """Files touched by a single commit (relative to repo root)."""
    try:
        result = subprocess.run(
            [
                "git", "-C", str(repo_root), "show",
                "--name-only", "--format=", sha,
            ],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    return {ln.strip() for ln in result.stdout.splitlines() if ln.strip()}


def _commit_has_plan_trailer(repo_root: Path, sha: str) -> bool:
    """True iff the commit message contains any ``Plan: <slug>#sN`` trailer."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%B", sha],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return bool(_PLAN_TRAILER_RE.search(result.stdout))


def _recent_commit_shas(repo_root: Path, n: int = 20) -> list[str]:
    """Short SHAs of the last ``n`` commits, newest-first."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", f"-n{n}", "--format=%h"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]


def _recent_commits_with_time(
    repo_root: Path, n: int = 20
) -> list[tuple[str, int]]:
    """``(short_sha, committer_unix_ts)`` for the last ``n`` commits.

    Newest-first. Used to filter out commits made before the current
    session began so the missing-trailer detector doesn't bleed into
    work owned by other sessions.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", f"-n{n}", "--format=%h %ct"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    out: list[tuple[str, int]] = []
    for ln in result.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        sha, _, ts = ln.partition(" ")
        try:
            out.append((sha, int(ts)))
        except ValueError:
            continue
    return out


def _session_started_at(sid: str, cwd: Path) -> float | None:
    """Unix timestamp of this session's first transcript record.

    Reads the first line of ``~/.claude/projects/<encoded-cwd>/<sid>.jsonl``
    and parses its ``timestamp`` field. Returns ``None`` on any failure;
    callers must treat that as "no time bound" rather than "before
    epoch", so synthetic test sessions (no on-disk transcript) keep
    their existing semantics.
    """
    try:
        encoded = str(Path(cwd).resolve()).replace("/", "-")
        path = Path.home() / ".claude" / "projects" / encoded / f"{sid}.jsonl"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
        if not first.strip():
            return None
        record = json.loads(first)
        ts = record.get("timestamp")
        if not isinstance(ts, str) or not ts:
            return None
        from datetime import datetime
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


#: Below this confidence, even a "done" verdict is parked into
#: pending-attributions instead of being acted on. Tunable; 0.6 is the
#: hand-picked starting point — closure_judgment's prompt nudges the
#: model to use ≥0.7 for done verdicts, so the threshold rejects
#: visibly-uncertain calls.
_JUDGMENT_CONFIDENCE_FLOOR = 0.6

#: Default model for the closure judgment call. Resolved at call time
#: from `LORE_CLOSURE_JUDGMENT_MODEL` env var; this is the fallback.
_DEFAULT_CLOSURE_MODEL = "claude-haiku-4-5-20251001"


def _commit_diff_summary(repo_root: Path, sha: str) -> str:
    """Return ``git show --stat`` output for ``sha`` (truncated)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", "--stat", "--format=", sha],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    text = result.stdout.strip()
    # Cap to keep the prompt small — diff stats over a few KB are rare
    # and the model only needs the file list + line counts.
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated)"
    return text


def _commit_message_full(repo_root: Path, sha: str) -> str:
    """Return the full commit message body for ``sha``."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%B", sha],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _append_pending_attribution(
    *,
    sid: str,
    commit_sha: str,
    plan_slug: str,
    step_id: str,
    decision: str,
    confidence: float,
    reason: str,
) -> None:
    """Persist one attribution row to the per-session pending-attr cache.

    Read-modify-write under best-effort error handling — the cache is
    a hint surface for the next session, not authoritative state, so
    a malformed file falls back to "start fresh."
    """
    from datetime import UTC, datetime as _dt

    cache_path = (
        Path.home() / ".cache" / "lore" / "sessions" / sid
        / "pending-attributions.json"
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if cache_path.exists():
            try:
                loaded = json.loads(cache_path.read_text())
                if isinstance(loaded, list):
                    existing = [e for e in loaded if isinstance(e, dict)]
            except (json.JSONDecodeError, OSError):
                existing = []
        existing.append({
            "commit_sha": commit_sha,
            "plan_slug": plan_slug,
            "step_id": step_id,
            "decision": decision,
            "confidence": float(confidence),
            "reason": reason,
            "judged_at": _dt.now(UTC).isoformat(),
        })
        cache_path.write_text(json.dumps(existing, indent=2))
    except OSError:
        return


def _pending_attributions_block(
    wiki_root: Path, *, repo: str | None
) -> list[str]:
    """Render unresolved attributions from prior sessions as a SessionStart block.

    Reads every ``pending-attributions.json`` under
    ``~/.cache/lore/sessions/*/`` and surfaces the entries whose
    ``plan_slug`` is currently active and matches ``repo`` (or is a
    repo-less wiki-general plan). Dedups across sessions on the
    ``(commit_sha, plan_slug, step_id)`` key — a triple parked twice
    is one issue, not two.

    Returns the rendered lines (no trailing blank). Empty list when
    nothing actionable remains. Always best-effort: malformed cache
    files are skipped silently.
    """
    try:
        from lore_core.plans.registry import list_active

        cache_root = Path.home() / ".cache" / "lore" / "sessions"
        if not cache_root.exists():
            return []

        cards = list_active(wiki_root, repo=repo)
        if not cards:
            return []
        active_slugs = {c.slug for c in cards}

        seen: set[tuple[str, str, str]] = set()
        rows: list[dict] = []
        for sid_dir in sorted(cache_root.iterdir()):
            if not sid_dir.is_dir():
                continue
            cache_path = sid_dir / "pending-attributions.json"
            if not cache_path.exists():
                continue
            try:
                payload = json.loads(cache_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, list):
                continue
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                slug = entry.get("plan_slug")
                if not isinstance(slug, str) or slug not in active_slugs:
                    continue
                sha = str(entry.get("commit_sha") or "")
                step_id = str(entry.get("step_id") or "")
                if not sha or not step_id:
                    continue
                key = (sha, slug, step_id)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(entry)

        if not rows:
            return []

        out = [
            "## ⚠ Unresolved plan attributions from recent sessions",
            "",
        ]
        for entry in rows[:10]:  # cap to keep banner bounded
            sha = str(entry.get("commit_sha") or "")
            slug = str(entry.get("plan_slug") or "")
            step_id = str(entry.get("step_id") or "")
            decision = str(entry.get("decision") or "")
            reason = str(entry.get("reason") or "")
            try:
                conf = float(entry.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            out.append(
                f"- commit `{sha}` ↔ [[plan/{slug}#{step_id}]]: "
                f"{decision} ({conf:.1f}) — {reason}"
            )
        if len(rows) > 10:
            out.append(f"- … +{len(rows) - 10} more")
        out.append("")
        out.append(
            "Resolve: `git commit --amend` adding "
            "`Plan: <slug>#<step>` (closes), or run "
            "`/lore:plan-step --done` for the right anchor. "
            "Each entry will clear once acted on."
        )
        return out
    except Exception:  # noqa: BLE001 — never break SessionStart
        return []


def _attribute_commits_with_judgment(cwd_path: Path) -> list[str]:
    """LLM-gated commit→step attribution at Stop.

    For each recent commit since this session began:

    * If it carries a ``Plan:`` trailer, the existing
      :func:`_plan_trailer_nudges_for_stop` path handles it — skip.
    * Otherwise, intersect the commit's changed files against each
      active plan's ``step_files``. For each non-empty overlap on a
      not-yet-done step, ask :func:`closure_judgment.judge_closure`.
    * ``done`` + confidence ≥ ``_JUDGMENT_CONFIDENCE_FLOOR`` →
      :func:`set_step` ``DONE`` and emit a confirmation line.
    * ``in_progress`` + confidence ≥ floor →
      :func:`set_step` ``IN_PROGRESS`` (idempotent on already-in-progress).
    * Anything else (low confidence, ``skip``, no client, LLM error)
      → write the row to ``pending-attributions.json`` for the next
      session to surface.

    Per-session seen-set at
    ``~/.cache/lore/sessions/<sid>/plan-judgment.seen`` keeps each
    ``(sha, slug, step)`` triple from re-firing. ``set_step`` is itself
    idempotent so the seen-set is belt-and-braces.

    Returns a (possibly empty) list of confirmation lines for the user.
    Always best-effort: any uncaught exception → ``[]``.
    """
    # Env-var ops override: LORE_DISABLE_LLM_JUDGMENT=1 wins over config.
    # Primary user knob is ``curator.closure_judgment_enabled`` in
    # ``$LORE_ROOT/.lore/config.yml`` — checked below once we have a
    # resolved scope/lore_root.
    if os.environ.get("LORE_DISABLE_LLM_JUDGMENT") == "1":
        return []
    try:
        from lore_core.drain import resolve_session_id
        from lore_core.git import git_repo_root
        from lore_core.plans.registry import list_active
        from lore_core.plans.step_status import set_step
        from lore_core.plans.types import StepStatus
        from lore_core.root_config import load_root_config
        from lore_curator.closure_judgment import judge_closure
        from lore_curator.defrag_curator import _resolve_backend
        from lore_curator.llm_client import LlmClientError, make_llm_client

        scope = resolve_scope(cwd_path)
        if scope is None:
            return []
        # Config-level kill switch — primary user knob. Read once;
        # also reused below for backend resolution to avoid double
        # config loads on the hot Stop-hook path.
        _lore_root_for_llm = _infer_lore_root(scope.claude_md_path)
        try:
            if not load_root_config(_lore_root_for_llm).curator.closure_judgment_enabled:
                return []
        except Exception:  # noqa: BLE001 — never break the hook on config load
            pass
        wiki_root = get_wiki_root() / scope.wiki
        if not wiki_root.exists():
            return []
        repo_slug = current_repo(cwd_path)
        repo_root = git_repo_root(cwd_path)
        if repo_root is None:
            return []

        cards = list_active(wiki_root, repo=repo_slug)
        if not cards:
            return []

        # Pre-build (slug, step_id, file-set) targets for cards with
        # any step_files declared. Cards without step_files contribute
        # nothing — by design (no signal, no auto-attribution).
        plan_targets: list[tuple[Any, str, set[str]]] = []
        for card in cards:
            for step_id, files in card.step_files.items():
                if not files:
                    continue
                plan_targets.append((card, step_id, set(files)))
        if not plan_targets:
            return []

        sid, _ = resolve_session_id(cwd_path)
        seen_path = (
            Path.home() / ".cache" / "lore" / "sessions" / sid
            / "plan-judgment.seen"
        )
        seen = _read_nudge_seen_set(seen_path)
        session_floor = _session_started_at(sid, cwd_path)

        # Resolve LLM client lazily; None = graceful degradation path.
        # Honour curator.backend in .lore/config.yml — without this, a
        # no-arg make_llm_client() auto-probes and returns SubprocessClient
        # whenever `claude` is on PATH, which then spawns claude -p whose
        # own Stop hook recurses back into this function (b873843).
        try:
            llm_client = make_llm_client(
                backend=_resolve_backend(None, _lore_root_for_llm),
                lore_root=_lore_root_for_llm,
            )
        except LlmClientError:
            llm_client = None
        model = os.environ.get(
            "LORE_CLOSURE_JUDGMENT_MODEL", _DEFAULT_CLOSURE_MODEL
        )

        confirmations: list[str] = []
        new_keys: list[str] = []
        for sha, ct in _recent_commits_with_time(repo_root, n=20):
            if session_floor is not None and ct < session_floor:
                continue
            if _commit_has_plan_trailer(repo_root, sha):
                continue
            commit_files = _commit_files(repo_root, sha)
            if not commit_files:
                continue
            for card, step_id, step_files in plan_targets:
                if not (commit_files & step_files):
                    continue
                # Skip already-done steps.
                if card.step_status.get(step_id) == "done":
                    continue
                key = f"{sha}!judged#{card.slug}#{step_id}"
                if key in seen:
                    continue

                # Decide path: LLM available → ask; otherwise park to pending.
                if llm_client is None:
                    _append_pending_attribution(
                        sid=sid,
                        commit_sha=sha,
                        plan_slug=card.slug,
                        step_id=step_id,
                        decision="skip",
                        confidence=0.0,
                        reason="no LLM client available",
                    )
                    new_keys.append(key)
                    continue

                # Run the LLM judgment.
                try:
                    judgment = judge_closure(
                        commit_sha=sha,
                        commit_msg=_commit_message_full(repo_root, sha),
                        diff_summary=_commit_diff_summary(repo_root, sha),
                        plan_slug=card.slug,
                        step_id=step_id,
                        step_title=step_id,  # registry doesn't carry titles
                        step_body="",         # body lookup is heavy; skip for now
                        current_status=card.step_status.get(step_id, "pending"),
                        llm_client=llm_client,
                        model=model,
                    )
                except (LlmClientError, ValueError) as exc:
                    _append_pending_attribution(
                        sid=sid,
                        commit_sha=sha,
                        plan_slug=card.slug,
                        step_id=step_id,
                        decision="skip",
                        confidence=0.0,
                        reason=f"LLM error: {exc}",
                    )
                    new_keys.append(key)
                    continue

                # Apply decision.
                if (
                    judgment.decision in ("done", "in_progress")
                    and judgment.confidence >= _JUDGMENT_CONFIDENCE_FLOOR
                ):
                    target_status = (
                        StepStatus.DONE if judgment.decision == "done"
                        else StepStatus.IN_PROGRESS
                    )
                    # Idempotency: don't regress in_progress to in_progress
                    # noisily; set_step is fast-path on identical writes.
                    try:
                        set_step(
                            wiki_root=wiki_root,
                            slug=card.slug,
                            step_id=step_id,
                            status=target_status,
                        )
                        if judgment.decision == "done":
                            confirmations.append(
                                f"✓ marked plan/{card.slug}#{step_id} done "
                                f"from commit {sha} (LLM: {judgment.reason})"
                            )
                        else:
                            confirmations.append(
                                f"→ marked plan/{card.slug}#{step_id} "
                                f"in_progress from commit {sha}"
                            )
                    except (FileNotFoundError, ValueError, OSError):
                        # Plan vanished or step ID unknown — never break Stop.
                        pass
                else:
                    # Low confidence or skip → park for the next session.
                    _append_pending_attribution(
                        sid=sid,
                        commit_sha=sha,
                        plan_slug=card.slug,
                        step_id=step_id,
                        decision=judgment.decision,
                        confidence=judgment.confidence,
                        reason=judgment.reason,
                    )
                new_keys.append(key)

        if new_keys:
            _append_nudge_seen_set(seen_path, new_keys)
        return confirmations
    except Exception:  # noqa: BLE001 — never break Stop
        return []


def _read_nudge_seen_set(path: Path) -> set[str]:
    """Read the per-session nudge-seen file. Returns empty set on any error."""
    try:
        return {ln.strip() for ln in path.read_text().splitlines() if ln.strip()}
    except (OSError, UnicodeDecodeError):
        return set()


def _append_nudge_seen_set(path: Path, keys: list[str]) -> None:
    """Append nudge keys to the seen file. Best-effort; never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for k in keys:
                f.write(k + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def _first_line(text: str) -> str:
    """Return the first non-empty line, stripped."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _emit(hook_event: str, text: str, *, plain: bool) -> None:
    """Emit hook output in the format Claude Code expects.

    The authoritative schema (docs.claude.com, 2026-04) differs per event:

      SessionStart — both `systemMessage` (top-level, visible banner to
        the user and injected as context to Claude) and
        `hookSpecificOutput.additionalContext` (quietly injected full
        body for the agent to consume) are allowed. We use both so the
        user sees a one-liner in the transcript and the agent gets the
        full focus/open-items context.

      PreCompact — `hookSpecificOutput` is NOT allowed for this event.
        Only top-level fields (`systemMessage`, `continue`, etc.) are
        valid. We pack the open-items summary into `systemMessage` —
        which Claude Code injects as context on the next turn per the
        docs, so it survives the compaction boundary.

      Stop — `hookSpecificOutput` is NOT allowed. Only top-level fields.
        We emit the hint via `systemMessage`.

    `--plain` dumps raw text to stdout — used by the /lore:context skill and
    for manual inspection.
    """
    if plain:
        if text:
            sys.stdout.write(text)
            if not text.endswith("\n"):
                sys.stdout.write("\n")
        return
    if not text:
        return

    one_liner = _first_line(text)
    envelope: dict

    if hook_event == "SessionStart":
        from datetime import UTC as _UTC, datetime as _dt
        ts_hm = _dt.now(_UTC).strftime("%H:%M")
        log_text = f"── SessionStart {ts_hm} ──\n{text}"
        cc_pid = _claude_code_pid() or os.getppid()
        try:
            atomic_write_text(_cache_path_for_pid(cc_pid), log_text)
        except OSError:
            pass
        try:
            _gc_sessions_cache()
        except OSError:
            pass
        context_text = text
        if len(context_text) > MAX_CONTEXT_CHARS:
            context_text = (
                context_text[: MAX_CONTEXT_CHARS - 40]
                + "\n... (truncated — /lore:context for full)"
            )
        envelope = {
            "systemMessage": one_liner,
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context_text,
            },
        }
    elif hook_event == "Stop":
        envelope = {"systemMessage": text.strip()}
    else:
        # PreCompact and any future events — systemMessage only.
        envelope = {"systemMessage": text}

    sys.stdout.write(json.dumps(envelope))
    sys.stdout.write("\n")


_HOOK_EVENT = {
    "session-start": "SessionStart",
    "pre-compact": "PreCompact",
    "stop": "Stop",
}


def _hook_failure_banner(
    hook_event: str,
    exc: BaseException,
    *,
    log_path: Path | None = None,
) -> str:
    """Build a user-friendly diagnostic for a crashed hook.

    Claude Code surfaces stderr + traceback as
    "Failed with non-blocking status code" when a hook exits non-zero.
    That's noise without a next step. The shield catches the exception,
    feeds the banner through `_emit` like normal, and exits 0 — the user
    sees an actionable message instead of a traceback.

    Common causes named explicitly: stale install (templates / package
    data missing under pipx wheels) and binary-vs-plugin-cache drift.

    ``log_path`` (when provided) appears on a final ``Full traceback:``
    line so the maintainer / user can attach the file to a GitHub issue.
    Persisted by ``_crash_log.write_crash`` from both the per-hook
    shield and the ``__main__`` top-level backstop.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc) or "(no message)"
    # Truncate noisy paths/values so the banner stays readable.
    if len(exc_msg) > 200:
        exc_msg = exc_msg[:197] + "..."
    banner = (
        f"⚠ lore {hook_event} hook failed: {exc_name}: {exc_msg}\n"
        "\n"
        "Likely causes + fixes:\n"
        "  • Stale install (e.g. templates not bundled): "
        "[bold]lore install --upgrade[/bold] (or re-run install.sh).\n"
        "  • Binary vs plugin-cache drift: "
        "[bold]lore doctor[/bold] flags it and prints the exact command.\n"
        "  • If the error persists, file an issue: "
        "https://github.com/buchbend/lore/issues\n"
    )
    if log_path is not None:
        banner += f"\nFull traceback: {log_path}\n"
    banner += (
        "\nLore continues without its SessionStart banner — your session "
        "is otherwise unaffected."
    )
    return banner


def _shield_hook(typer_event: str):
    """Decorator: wrap a hook entry point so unexpected exceptions
    surface a friendly diagnostic via `_emit` and exit 0.

    Local try/except blocks scattered through the hook bodies catch
    *expected* failures (offer-rendering, drain breadcrumbs, curator
    spawns). The shield is the backstop for *unexpected* ones —
    NameError, FileNotFoundError on a bundled resource, etc. — that
    would otherwise leak a Python traceback into Claude Code's UI.

    `typer_event` is the Claude Code event name (SessionStart,
    PreCompact, Stop, UserPromptSubmit) so the banner names what
    failed precisely.
    """
    from functools import wraps

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (typer.Exit, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:  # noqa: BLE001 - hook crash shield
                # Best-effort: emit via the same JSON envelope used on
                # the happy path. If even that fails, fall through to
                # plain stderr — never re-raise (the whole point of
                # the shield is "no traceback in the user's session").
                plain = bool(kwargs.get("plain", False))
                # Persist traceback to disk so doctor + the user can
                # find it later. write_crash returns None silently if
                # the cache dir isn't writable — don't let that mask
                # the original failure.
                try:
                    from lore_cli._crash_log import write_crash
                    log_path = write_crash(typer_event, exc)
                except Exception:  # noqa: BLE001
                    log_path = None
                banner = _hook_failure_banner(typer_event, exc, log_path=log_path)
                try:
                    _emit(typer_event, banner, plain=plain)
                except Exception:  # noqa: BLE001
                    sys.stderr.write(
                        f"lore {typer_event} hook crashed and the diagnostic "
                        f"emitter also failed: {type(exc).__name__}: {exc}\n"
                    )
                # Exit 0 so Claude Code doesn't show 'non-blocking status code'.
                return None
        return wrapped
    return decorator


import typer  # noqa: E402

from lore_adapters import get_adapter  # noqa: E402
from lore_core.hook_log import HookEventLogger  # noqa: E402
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry, WikiLedger  # noqa: E402
from lore_core.scope_resolver import resolve_scope  # noqa: E402
from lore_cli._argv_compat import argv_main  # noqa: E402

hook_app = typer.Typer(
    add_completion=False,
    help="Internal hook dispatcher — invoked by Claude Code at SessionStart, PreCompact, etc.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _resolve_cwd(explicit: str | None) -> str:
    """Resolve CWD: explicit --cwd → $CLAUDE_PROJECT_DIR → $CURSOR_PROJECT_DIR → os.getcwd().

    Cursor exposes ``CLAUDE_PROJECT_DIR`` as an alias automatically (per
    cursor.com/docs/hooks), so the explicit ``CURSOR_PROJECT_DIR`` lookup
    is a defensive fallback for older Cursor versions or partial-compat
    environments where the alias hasn't been set up.
    """
    return (
        explicit
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("CURSOR_PROJECT_DIR")
        or os.getcwd()
    )


def _in_curator_mode() -> bool:
    return os.environ.get("LORE_CURATOR_MODE") == "1"


def _session_off_all() -> bool:
    """True iff `/lore:off` (scope=all) is active for the current session.

    Resolves the session id from CLAUDE_SESSION_ID, which `_read_hook_payload`
    publishes from the Claude Code stdin payload (issue #29 / v0.13.1).
    Returns False when no sid is available — without a sid we can't scope a
    sentinel, so the safe default is "not muted."
    """
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        return False
    from lore_core.toggles import is_off
    return is_off("all", sid)


def _journal_directive_lines() -> list[str]:
    """Invitation directive for the AI journal when the feature flag is on.

    Returns the lines appended to the SessionStart additionalContext
    directive cluster when ``journal.enabled`` is set in the root
    config. Trigger-moment framing rather than passive permission:
    names concrete moments where a gut-check ("anything to save?")
    is worth pausing for, and points the lens both inward (about
    self) and outward (about the user, the work, the conversation).
    Off by default; enable with ``lore journal enable``.
    """
    try:
        from lore_core import journal
        if not journal.enabled():
            return []
    except Exception:  # noqa: BLE001 — never break SessionStart
        return []
    return [
        "- **AI Journal active.** Scratch space — not deliverable, "
        "not extraction. `lore_journal_write` (kind=`ai`) appends "
        "here. Pause and ask *anything to save?* when:",
        "  - **about the work** — a smell, pattern, or design tension "
        "bigger than the immediate task",
        "  - **about yourself** — you got corrected on a substantive "
        "point, built on a wrong premise, finished a synthesis and "
        "noticed something generalizable",
        "  - **about the user** — they pivoted in a way you couldn't "
        "predict (what heuristic were they using?), their framing was "
        "unusually sharp or unusually leaky, their design has a "
        "tension worth naming, their working style produced or "
        "prevented something",
        "  - **about the conversation** — a prompt shape that worked "
        "or didn't, a feedback loop that surprised you, an "
        "interaction pattern worth replicating",
        "  - **just because** — a joke landed, the codebase did "
        "something funny, you formed an opinion",
        "  Default toward writing. Filler is cheap; lost observations "
        "aren't. The reader is future-you, not the user — write "
        "candidly. Criticism of the user's design or thinking belongs "
        "here when it's real; sycophancy doesn't.",
        "",
    ]


def _citation_directive_lines() -> list[str]:
    """Suppression directive for `/lore:off citations`.

    Returns the lines appended to the SessionStart additionalContext
    directive cluster when the citations sentinel is set for this
    session, else an empty list. The line tells the agent to skip the
    inline `› consulted [[X]]` affordance for the rest of the session.
    """
    sid = os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        return []
    from lore_core.toggles import is_off
    if not is_off("citations", sid):
        return []
    return [
        "- Inline citations are silenced this session — do not emit "
        "`› consulted [[X]]` lines after consulting the vault.",
        "",
    ]


def _read_hook_payload() -> dict:
    """Consume the JSON payload Claude Code passes on stdin for every hook fire.

    The payload carries the canonical ``session_id`` (and ``cwd``,
    ``transcript_path``, ``hook_event_name``). Until v0.12.1 lore never
    read it, so :func:`lore_core.drain.resolve_session_id` had to fall
    back to a transcript-freshness heuristic — fine for a single
    session, but with multiple concurrent Claude sessions the freshest
    transcript at curator-write time and at heartbeat-read time can
    disagree, leaving curator-filed notes stranded in the wrong drain
    file (issue #29).

    Side effect: when the payload provides ``session_id`` we publish it
    as ``CLAUDE_SESSION_ID`` so :func:`resolve_session_id`'s priority-2
    branch (and any spawned curator subprocess inheriting the env)
    pick it up without further plumbing. We never overwrite an
    explicit ``CLAUDE_SESSION_ID`` already in the env — a host that
    sets it directly stays authoritative.

    No-ops on a TTY (manual ``lore hook ... --plain`` runs) and on any
    parse error; hooks must never abort because of payload trouble.
    """
    try:
        if sys.stdin.isatty():
            return {}
    except (OSError, ValueError):
        return {}
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    sid = payload.get("session_id")
    if (
        isinstance(sid, str)
        and sid
        and not os.environ.get("CLAUDE_SESSION_ID")
    ):
        os.environ["CLAUDE_SESSION_ID"] = sid
    return payload


@hook_app.command("session-start")
@_shield_hook("SessionStart")
def cmd_session_start(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
    probe: bool = typer.Option(
        False,
        "--probe",
        hidden=True,
        help="Suppress all side-effects; used by lore doctor.",
    ),
) -> None:
    """Inject vault context at session start."""
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        return
    cwd_resolved = Path(_resolve_cwd(cwd))
    out = _session_start(str(cwd_resolved))

    # Surface pending `.lore.yml` offers at the top of the banner.
    # Defensive: offer rendering reads multiple files and classifies state;
    # we explicitly never let any failure here crash the SessionStart hook.
    try:
        notice = _offer_notice_line(cwd_resolved)
        if notice:
            out = notice + "\n\n" + out
    except Exception:  # noqa: BLE001 - hook must never crash SessionStart
        pass

    # Resolve scope once — reused by the banner, curator spawns, and
    # transcript sync below. None means "unattached cwd."
    scope = resolve_scope(cwd_resolved)
    lore_root = _infer_lore_root(scope.claude_md_path) if scope is not None else None

    if scope is None and not probe:
        _nudge_unattached(cwd_resolved, out)

    # Cross-host auto-pull (Phase 10 / 0.11.0).
    # Fast-forward this scope's wiki repo from origin if the wiki opted in
    # via .lore-wiki.yml's git.auto_pull (default true). Strictly read-only
    # on dirty/diverged trees — never disrupts the user's in-flight work.
    # Warning rendered into the banner footer so the user sees diverged
    # state surfaced; otherwise silent.
    auto_pull_warning: str | None = None
    if scope is not None and lore_root is not None and not probe:
        try:
            auto_pull_warning = _maybe_auto_pull_for_scope(scope, lore_root)
        except Exception:  # noqa: BLE001 — pull must never crash SessionStart
            auto_pull_warning = None

    # Buffer-and-flush handover-poll: when a sibling session ended
    # mid-flush, wait briefly for ``state=closed`` so the resulting
    # wikilink lands in this SessionStart's context rather than only in
    # the next heartbeat. Phase 1 of flush is sub-1s by construction; a
    # 5s budget covers worst-case I/O. Phase 2 (LLM rewrite) happens
    # transparently after the handover unblocks.
    if scope is not None and lore_root is not None and not probe:
        try:
            handover_lines = _poll_buffer_handover(
                lore_root, cwd_resolved, timeout_s=5.0,
            )
            if handover_lines:
                out = out + "\n\n" + "\n".join(handover_lines)
        except Exception:  # noqa: BLE001 - handover must never crash SessionStart
            pass

    # Attempt to append capture-state breadcrumb banner
    try:
        from datetime import UTC, datetime as dt
        from lore_cli.breadcrumb import BannerContext, render_banner

        if scope is not None and lore_root is not None:
            wiki_root = get_wiki_root()
            if wiki_root.exists():
                wiki_cfg = _load_wiki_cfg_from_scope(scope, lore_root)
                now = dt.now(tz=UTC)

                # Count notes in scope if possible (optional for v1)
                note_count = 0
                try:
                    wiki_path = wiki_root / scope.wiki
                    catalog = _wiki_catalog(wiki_path)
                    if catalog:
                        note_count = catalog.get("stats", {}).get("total_notes", 0)
                except (KeyError, TypeError, AttributeError):
                    # Catalog shape is best-effort — never block banner on it.
                    pass

                ctx = BannerContext(
                    lore_root=lore_root,
                    scope=scope,
                    wiki_config=wiki_cfg,
                    now=now,
                    note_count=note_count,
                )
                banner = render_banner(ctx)
                if banner is not None:
                    out = out + "\n\n" + banner

                # P5b: appended drain lines — "this session" and "since you
                # left." Skipped under `--probe` because the rendering
                # writes cursor files (cold-start init + post-render
                # advance), and `lore doctor` must leave no on-disk
                # footprint.
                if not probe:
                    try:
                        drain_lines = _render_drain_lines(lore_root, cwd_resolved)
                        if drain_lines:
                            out = out + "\n" + "\n".join(drain_lines)
                    except (OSError, json.JSONDecodeError):
                        pass

                try:
                    cross = _cross_scope_breadcrumbs(lore_root, scope.wiki)
                    if cross:
                        out = out + "\n" + "\n".join(cross)
                except (OSError, json.JSONDecodeError):
                    pass
    except Exception:  # noqa: BLE001 - banner is presentation; never block SessionStart on it
        pass

    if auto_pull_warning is not None:
        out = out + "\n" + auto_pull_warning

    # Side-effect spawns — suppressed under --probe.
    if not probe and scope is not None and lore_root is not None:
        # Auto-trigger Curator B on calendar-day rollover.
        try:
            from datetime import UTC, datetime as dt

            wledger = WikiLedger(lore_root, scope.wiki)
            wentry = wledger.read()
            today = dt.now(UTC).date()
            last_b_date = wentry.last_curator_b.date() if wentry.last_curator_b else None
            if last_b_date is None or today > last_b_date:
                cfg_b = _load_wiki_cfg_from_scope(scope, lore_root)
                _spawn_detached_curator_b(
                    lore_root, scope.wiki, cooldown_s=cfg_b.curator.curator_b_cooldown_s
                )
        except Exception:
            pass

        # Fire-and-forget transcript mirror (P4a). Idempotent, gitignored
        # destination, own spawn lock.
        try:
            _spawn_detached_transcript_sync(lore_root)
        except Exception:
            pass

        # Auto-trigger Curator C weekly (UTC ISO-week + per-user 48h jitter).
        # Flag-gated off by default; see project_curator_triad + spec §6.
        try:
            cfg = _load_wiki_cfg_from_scope(scope, lore_root)
            c_cfg = cfg.curator.curator_c
            if c_cfg.enabled:
                if c_cfg.mode != "local":
                    HookEventLogger(lore_root).emit(
                        event="curator-c",
                        outcome="central-mode-skipped",
                        error={
                            "message": "mode=central deferred to v2; local spawn skipped",
                            "wiki": scope.wiki,
                        },
                    )
                else:
                    wledger = WikiLedger(lore_root, scope.wiki)
                    wentry = wledger.read()
                    now = _now_utc()
                    last_c = wentry.last_curator_c
                    if last_c is not None and last_c.tzinfo is None:
                        from datetime import UTC as _UTC
                        last_c = last_c.replace(tzinfo=_UTC)
                    iso_now = now.isocalendar()
                    needs_rollover = (
                        last_c is None
                        or last_c.isocalendar()[:2] != iso_now[:2]
                    )
                    if needs_rollover:
                        monday = _iso_week_monday_utc(now)
                        offset = _curator_c_jitter_seconds(_curator_c_email())
                        from datetime import timedelta as _td
                        if now >= monday + _td(seconds=offset):
                            _spawn_detached_curator_c(lore_root)
        except Exception:
            pass

    # Phase 6: project orientation auto-injection. When SessionStart
    # fires inside an attached scope and a project orientation note
    # exists at ``projects/<slug>/<slug>.md`` (folder layout, post-
    # migration) OR legacy flat ``projects/<slug>.md``, append its
    # body (frontmatter stripped, capped at ORIENTATION_BUDGET_CHARS)
    # to the LLM context block. Concepts/decisions/threads/plans/
    # sessions stay pull-on-demand — only the short orientation auto-
    # loads. User-facing status line is unchanged.
    if scope is not None and not probe:
        try:
            orientation_block = _render_project_orientation(
                scope, get_wiki_root(),
            )
            if orientation_block:
                out = out + "\n\n" + orientation_block
        except Exception:  # noqa: BLE001 - orientation must never crash SessionStart
            pass

    _emit("SessionStart", out, plain=plain)


def _render_project_orientation(scope: "Scope", wiki_root: Path) -> str | None:
    """Read the project orientation note for ``scope`` and return a
    formatted block for SessionStart context injection.

    Lookup order (Phase 6 + dual-mode tolerance):
      1. ``projects/<slug>/<slug>.md`` (folder layout, post-migration)
      2. ``projects/<slug>.md``        (legacy flat)

    Slug = scope's last colon-separated segment (e.g.
    ``ccat:data-center:ops-db`` → ``ops-db``). Frontmatter is stripped.
    Body is capped at :data:`ORIENTATION_BUDGET_CHARS`.

    Returns None when no orientation note exists, the scope chain has
    no last segment, or the wiki root is missing.
    """
    if not wiki_root.exists():
        return None
    if not scope or not scope.scope:
        return None
    slug = scope.scope.rsplit(":", 1)[-1]
    # Defense-in-depth: only allow conservative slug shapes. The scope
    # value should already come from a curated source (`_scopes.yml`
    # via the registry), but a malformed entry could otherwise put
    # path-traversal segments into the slug we feed straight into a
    # ``Path()`` join.
    import re
    if not slug or not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        return None
    wiki_path = wiki_root / scope.wiki
    candidates = [
        wiki_path / "projects" / slug / f"{slug}.md",
        wiki_path / "projects" / f"{slug}.md",
    ]
    for path in candidates:
        if path.is_file():
            try:
                from lore_core.schema import strip_frontmatter

                text = path.read_text(errors="replace")
                body = strip_frontmatter(text).strip()
            except OSError:
                return None
            if not body:
                return None
            if len(body) > ORIENTATION_BUDGET_CHARS:
                suffix = "\n... (orientation truncated — /lore:context for full)"
                body = body[: ORIENTATION_BUDGET_CHARS - len(suffix)] + suffix
            header = f"## Project: [[{slug}]]\n"
            return header + body
    return None


@hook_app.command("pre-compact")
@_shield_hook("PreCompact")
def cmd_pre_compact(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """Inject open items before compaction."""
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        return
    out = _pre_compact(_resolve_cwd(cwd))
    _emit("PreCompact", out, plain=plain)


@hook_app.command("stop")
@_shield_hook("Stop")
def cmd_stop(
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """Hint to capture a session note."""
    if _in_curator_mode():
        return
    payload = _read_hook_payload()
    if _session_off_all():
        return
    out = _stop()

    # Mid-session advance gap: emit ⚠ nudges for plan trailers landed
    # since the last nudge in this session. Per-session seen-set lives in
    # ~/.cache/lore/sessions/<sid>/plan-nudges.seen so the same commit
    # doesn't re-fire on every Stop hook until the user advances.
    cwd_str = (payload or {}).get("cwd")
    cwd_path = Path(cwd_str) if cwd_str else Path(os.getcwd())
    # Two complementary passes:
    #   1. Trailer-action: commits WITH `Plan:` trailers → auto-advance
    #      the step (writes step_status, may flip plan to status: done).
    #   2. Judgment-action: commits WITHOUT trailers whose files overlap
    #      a plan's step_files → LLM judgment closes the step (or parks
    #      the case to pending-attributions for the next session).
    # Both share the seen-set namespace prefix scheme so neither
    # re-fires on subsequent Stops.
    action_msgs = _plan_trailer_nudges_for_stop(cwd_path)
    judgment_msgs = _attribute_commits_with_judgment(cwd_path)
    plan_lines = action_msgs + judgment_msgs
    if plan_lines:
        if out:
            out = out.rstrip() + "\n\n"
        out += "\n".join(plan_lines)

    _emit("Stop", out, plain=plain)


@hook_app.command("context-log")
def cmd_context_log() -> None:
    """Print the context log — what Lore injected this session."""
    sys.stdout.write(_context_log())


@hook_app.command("live-state", hidden=True)
def cmd_live_state() -> None:
    """Deprecated alias for context-log."""
    sys.stdout.write(_context_log())


# ---------------------------------------------------------------------------
# UserPromptSubmit heartbeat
# ---------------------------------------------------------------------------


def _read_cursor(path: Path) -> "datetime | None":
    """Read a drain cursor file. Returns None if missing or unparseable."""
    if not path.exists():
        return None
    try:
        from datetime import datetime as _dt, UTC as _UTC
        raw = path.read_text().strip()
        if raw:
            return _dt.fromisoformat(raw).replace(tzinfo=_UTC)
    except (OSError, ValueError):
        pass
    return None


def _write_cursor(path: Path, ts: "datetime") -> None:
    """Atomic cursor write; best-effort."""
    from datetime import timedelta
    try:
        tmp = path.with_suffix(".cursor.tmp")
        tmp.write_text((ts + timedelta(microseconds=1)).isoformat())
        os.replace(tmp, path)
    except OSError:
        pass


def _heartbeat(
    lore_root: Path,
    cwd: Path,
    wiki_cfg: "WikiConfig",
    *,
    pid: int | None = None,
) -> tuple[str | None, str | None]:
    """Check drain for new events; return (system_message, additional_context).

    Reads both the system drain (background work) and session-scoped
    drain (notes filed for this session). Both may be None. Cooldown-
    gated: returns (None, None) when the stamp is fresh.
    """
    from lore_core.drain import SYSTEM_SESSION, DrainStore, resolve_session_id

    hb = wiki_cfg.heartbeat
    if not hb.enabled:
        return None, None

    stamp = lore_root / ".lore" / "curator-heartbeat.spawn.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if _stamp_within_cooldown(stamp, hb.cooldown_s):
        return None, None

    effective_pid = pid or _claude_code_pid() or os.getpid()
    drain_dir = lore_root / ".lore" / "drain"
    drain_dir.mkdir(parents=True, exist_ok=True)

    # Session cursor stays pid-keyed: each Claude OS process tracks its
    # own "have I shown this session event" mark, so two parallel
    # windows don't steal each other's notifications.
    sess_cursor_path = drain_dir / f"heartbeat-session-{effective_pid}.cursor"
    sess_cursor_ts = _read_cursor(sess_cursor_path)

    # System cursor is process-shared: events surface to whichever
    # reader (SessionStart or any heartbeat) gets there first, then
    # never again. ``read_or_init_cursor`` cold-starts to ``now`` so a
    # fresh install never reaches back through history.
    system_store = DrainStore(lore_root, SYSTEM_SESSION)
    sys_cursor_ts = system_store.read_or_init_cursor()
    system_events = system_store.read(since=sys_cursor_ts, limit=200)

    sid, _ = resolve_session_id(cwd)
    session_store = DrainStore(lore_root, sid)
    session_events = session_store.read(since=sess_cursor_ts, limit=200)

    events = system_events + session_events

    if not events:
        _write_stamp(stamp)
        return None, None

    counts = _tally_drain(events)
    summary = _format_drain_summary(counts, events)
    sys_msg = f"lore: {summary}" if summary else None

    ctx = None
    if hb.push_context and events:
        wikilinks = []
        for e in events:
            wl = e.data.get("wikilink")
            if wl:
                wikilinks.append(wl)
        if wikilinks:
            ctx = "New in vault: " + ", ".join(dict.fromkeys(wikilinks))

    if system_events:
        from datetime import timedelta
        newest = max(e.ts for e in system_events)
        system_store.write_cursor(newest + timedelta(microseconds=1))
    if session_events:
        _write_cursor(sess_cursor_path, max(e.ts for e in session_events))

    _write_stamp(stamp)
    return sys_msg, ctx


def _heartbeat_spawn_curator_a(
    lore_root: Path,
    scope: "Scope",
    *,
    cooldown_s: int = 120,
) -> str | None:
    """Evaluate the spawn-gate for the current scope's wiki; spawn if it crosses.

    Called from ``cmd_user_prompt_submit`` after the drain heartbeat. This is
    the mid-session snappy lever: long active sessions hit this every prompt,
    so accumulated turn count or stale pending-age can trigger Curator A
    without waiting for the next session-start/end boundary.

    Independent 120s cooldown stamp (``curator-heartbeat-spawn.stamp``) so
    this never thrashes regardless of prompt cadence. The actual spawn also
    runs through ``_spawn_detached_curator_a``'s own 60s lock+stamp, so two
    layers of rate-limiting prevent storms.

    Returns a reason string for telemetry, or None when no spawn was made.
    """
    stamp = lore_root / ".lore" / "curator-heartbeat-spawn.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if _stamp_within_cooldown(stamp, cooldown_s):
        return None

    try:
        tledger = TranscriptLedger(lore_root)
        buckets = tledger.pending_by_wiki()
    except Exception:
        return None

    entries = buckets.get(scope.wiki, [])
    if not entries:
        _write_stamp(stamp)
        return None

    try:
        wiki_cfg = _load_wiki_cfg_for_wiki(lore_root, scope.wiki)
    except Exception:
        return None

    should, reason = _wiki_should_spawn(entries, wiki_cfg, now=_now_utc())
    _write_stamp(stamp)
    if not should:
        return None

    spawned = _spawn_detached_curator_a(
        lore_root, cooldown_s=wiki_cfg.curator.curator_a_cooldown_s
    )
    return reason if spawned else None


@hook_app.command("user-prompt-submit")
@_shield_hook("UserPromptSubmit")
def cmd_user_prompt_submit(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """Lightweight heartbeat — check drain for new events."""
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        return
    cwd_resolved = Path(_resolve_cwd(cwd))
    scope = resolve_scope(cwd_resolved)
    if scope is None:
        return
    lore_root = _infer_lore_root(scope.claude_md_path)
    wiki_cfg = _load_wiki_cfg_from_scope(scope, lore_root)

    # Mid-session transcript discovery + mtime refresh. Closes the
    # SessionStart-vs-transcript-creation race (sub-second; SessionStart
    # can sample the projects dir before Claude Code has created the
    # transcript file) and keeps `last_mtime` fresh so `pending()` /
    # the spawn-gate see work growing across the session. Without this,
    # long sessions sit on accumulated turns until SessionEnd.
    try:
        adapter = get_adapter("claude-code")
        _register_pending_transcripts(lore_root, cwd_resolved, adapter=adapter)
    except Exception:
        pass  # never break the prompt path on a registration hiccup

    sys_msg, ctx = _heartbeat(lore_root, cwd_resolved, wiki_cfg)

    # Mid-session snappy spawn — evaluate the turn-aware gate every prompt
    # so long sessions don't sit on accumulated work waiting for session-end.
    # Independently rate-limited (120s heartbeat cooldown + 60s spawn lock).
    try:
        _heartbeat_spawn_curator_a(lore_root, scope)
    except Exception:
        pass  # never break the prompt path on a spawn-gate hiccup

    # Citations toggle takes effect mid-session: re-assert the suppression
    # directive on every prompt while `/lore:off citations` is active so the
    # agent sees it on the very next turn after the user toggles it,
    # rather than waiting for the next SessionStart.
    cite_lines = _citation_directive_lines()
    if cite_lines:
        cite_block = "\n".join(line for line in cite_lines if line)
        ctx = cite_block if not ctx else ctx + "\n\n" + cite_block

    if not sys_msg and not ctx:
        return

    if plain:
        if sys_msg:
            sys.stdout.write(sys_msg + "\n")
        if ctx:
            sys.stdout.write(ctx + "\n")
        return

    envelope: dict = {}
    if sys_msg:
        envelope["systemMessage"] = sys_msg
    if ctx:
        envelope["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ctx,
        }
    if envelope:
        sys.stdout.write(json.dumps(envelope) + "\n")

    if sys_msg:
        _append_context_log(sys_msg, ctx)


# ---------------------------------------------------------------------------
# Capture hook helpers
# ---------------------------------------------------------------------------


def _resolve_cwd_capture() -> Path:
    """Resolve CWD for capture: $CLAUDE_PROJECT_DIR → $CURSOR_PROJECT_DIR → os.getcwd()."""
    env = (
        os.environ.get("CLAUDE_PROJECT_DIR")
        or os.environ.get("CURSOR_PROJECT_DIR")
    )
    return Path(env) if env else Path(os.getcwd())


def _register_pending_transcripts(
    lore_root: Path,
    cwd: Path,
    *,
    adapter: Any,
    transcript: Path | None = None,
) -> None:
    """List transcripts for ``cwd`` and upsert into the ledger.

    Shared by ``capture`` (SessionStart/End/PreCompact) and
    ``user-prompt-submit`` (mid-session). Closes the SessionStart-vs-
    transcript-creation race for sessions whose transcript file did not
    exist when SessionStart sampled the directory: any subsequent
    UserPromptSubmit picks the missing entry up. mtime updates also
    propagate so ``pending()`` sees work growing across a long session
    and the heartbeat spawn-gate can fire mid-session for semantic
    capture rather than waiting for SessionEnd.

    Attach-time watermark: transcripts older than the attachment's
    ``attached_at`` are pre-stamped as already seen so only future
    sessions are pending. Use ``lore backfill`` to opt in to history.

    Bulk-upserted in one ledger serialisation regardless of how many
    handles change — keeps the call well within the hook budget.
    """
    from lore_core.state.attachments import AttachmentsFile

    if transcript is not None:
        handles = [h for h in adapter.list_transcripts(cwd) if h.path == transcript]
    else:
        handles = adapter.list_transcripts(cwd)

    if not handles:
        return

    tledger = TranscriptLedger(lore_root)
    af = AttachmentsFile(lore_root)
    af.load()
    attachment = af.longest_prefix_match(cwd)

    to_write: list[TranscriptLedgerEntry] = []
    for h in handles:
        entry = tledger.get(h.integration, h.id)
        if entry is None:
            is_historical = (
                attachment is not None
                and h.mtime < attachment.attached_at
            )
            to_write.append(
                TranscriptLedgerEntry(
                    integration=h.integration,
                    transcript_id=h.id,
                    path=h.path,
                    directory=h.cwd,
                    digested_hash=None,
                    digested_index_hint=None,
                    synthesised_hash=None,
                    last_mtime=h.mtime,
                    curator_a_run=attachment.attached_at if is_historical else None,
                    noteworthy=None,
                    session_note=None,
                )
            )
        elif entry.last_mtime != h.mtime:
            entry.last_mtime = h.mtime
            to_write.append(entry)
    if to_write:
        tledger.bulk_upsert(to_write)


def _nudge_unattached(cwd: Path, out: str) -> None:
    """One-time nudge when session starts in an unattached directory."""
    import hashlib
    nudge_dir = _cache_dir() / "nudged"
    cwd_hash = hashlib.sha256(str(cwd).encode()).hexdigest()[:16]
    marker = nudge_dir / cwd_hash
    if marker.exists():
        return
    try:
        lore_root = get_lore_root()
        from lore_core.state.attachments import AttachmentsFile
        af = AttachmentsFile(lore_root)
        af.load()
        if any(d.path == cwd for d in af._declined):
            return
    except (OSError, json.JSONDecodeError):
        # Couldn't read the declined list — fall through and consider nudging.
        pass
    try:
        nudge_dir.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass
    print(
        "lore: this directory is not attached — sessions won't be captured. "
        "Run /lore:attach to connect it.",
        file=sys.stderr,
    )


def _infer_lore_root(start: Path) -> Path:
    """Infer LORE_ROOT for a hook-context path.

    Precedence: ``$LORE_ROOT`` env var → walk-up → config-file → default.

    - **env** wins because it's explicit per-invocation; a user who
      exports ``LORE_ROOT=...`` is overriding for a reason.
    - **walk-up** beats config-file: when the hook has a path argument,
      that path is the explicit signal — a user with a global config-file
      pointing at ``~/personal-vault`` but currently editing inside
      ``~/work-vault/wiki/foo/`` should resolve to ``~/work-vault``.
    - **config-file → ~/lore default** via ``get_lore_root()`` when no
      walk-up ancestor contains ``wiki/``.

    Accepts either a file (CLAUDE.md) or a directory (cwd). Returns a
    resolved absolute path.
    """
    env = os.environ.get("LORE_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    starting_dir = start.parent if start.is_file() else start
    for parent in [starting_dir, *starting_dir.parents]:
        if (parent / "wiki").is_dir():
            return parent.resolve()
    return get_lore_root()


def _load_wiki_cfg_from_scope(scope, lore_root: Path):
    from lore_core.wiki_config import load_wiki_config
    wiki_dir = lore_root / "wiki" / scope.wiki
    return load_wiki_config(wiki_dir)


def _maybe_auto_pull_for_scope(scope, lore_root: Path) -> str | None:
    """Fast-forward this scope's wiki repo from origin if config opts in.

    Returns a one-line user-facing warning when the pull was skipped for
    a reason the user should know about (dirty tree, diverged history),
    or ``None`` for clean / silent outcomes (already in sync, no remote,
    pull succeeded). The caller renders the warning into the SessionStart
    banner so divergence is surfaced; auto-pull is otherwise transparent.
    """
    from lore_core.git_sync import SyncStatus, auto_pull
    from lore_core.wiki_config import load_wiki_config

    wiki_dir = lore_root / "wiki" / scope.wiki
    if not wiki_dir.exists():
        return None
    cfg = load_wiki_config(wiki_dir)
    if not cfg.git.auto_pull:
        return None

    result = auto_pull(wiki_dir)
    if result.status is SyncStatus.SKIPPED_DIRTY:
        return f"› wiki [[{scope.wiki}]] has uncommitted changes — auto-pull skipped"
    if result.status is SyncStatus.SKIPPED_DIVERGED:
        return f"› wiki [[{scope.wiki}]] diverged from origin — `git pull` manually"
    return None


def _offer_notice_line(cwd: Path) -> str | None:
    """Return a one-line notice when a ``.lore.yml`` offer is pending acceptance.

    Returns ``None`` if:
      - no ``.lore.yml`` covers ``cwd``;
      - an attachment with the matching fingerprint already exists
        (state=ATTACHED);
      - the offer was previously declined (state=DORMANT);
      - ``$LORE_ROOT`` cannot be located on this host.

    Logs a ``lore-yml-offered`` event when it does emit (OFFERED, DRIFT)
    so telemetry captures the prompt even if the user ignores it.
    """
    from lore_core.config import resolve_lore_root
    lore_root = resolve_lore_root()
    # Use resolve_lore_root (not get_lore_root) so a stale ~/lore from a
    # previous install does NOT trigger offer logic when the user has
    # neither $LORE_ROOT exported nor ~/.config/lore/config.yml set.
    if lore_root is None:
        return None

    try:
        from lore_core.consent import ConsentState, classify_state
        from lore_core.state.attachments import AttachmentsFile

        attachments = AttachmentsFile(lore_root)
        attachments.load()
        result = classify_state(cwd, attachments)
    except Exception:
        return None

    if result.state not in (ConsentState.OFFERED, ConsentState.DRIFT):
        return None

    try:
        HookEventLogger(lore_root).emit(
            event="lore-yml-offered",
            outcome=result.state.value,
            detail={
                "wiki": result.offer.wiki if result.offer else None,
                "scope": result.offer.scope if result.offer else None,
                "repo_root": str(result.repo_root) if result.repo_root else None,
                "offer_fingerprint": result.offer_fingerprint,
            },
        )
    except Exception:
        pass

    offer = result.offer
    assert offer is not None  # OFFERED/DRIFT imply offer present
    if result.state is ConsentState.OFFERED:
        return (
            f"lore: this repo offers attachment to wiki `{offer.wiki}` "
            f"(scope `{offer.scope}`). Run `/lore:attach` to accept or "
            f"`/lore:attach --decline` to dismiss."
        )
    # DRIFT
    return (
        f"lore: the `.lore.yml` offer for this repo has changed since you "
        f"attached (wiki `{offer.wiki}`, scope `{offer.scope}`). Run "
        f"`/lore:attach` to re-accept."
    )


def _load_wiki_cfg_for_wiki(lore_root: Path, wiki_name: str):
    """Load the config for `<lore_root>/wiki/<wiki_name>/.lore-wiki.yml`.

    Separate from `_load_wiki_cfg_from_scope` because per-wiki threshold
    checks need each wiki's own config, not just the scope the hook was
    invoked under.
    """
    from lore_core.wiki_config import load_wiki_config
    return load_wiki_config(lore_root / "wiki" / wiki_name)


def _stamp_within_cooldown(stamp: Path, cooldown_s: int) -> bool:
    """True if stamp exists and is younger than cooldown_s seconds."""
    import time as _time
    try:
        last = float(stamp.read_text().strip())
    except (OSError, ValueError):
        return False
    return (_time.time() - last) < cooldown_s


def _write_stamp(stamp: Path) -> None:
    """Atomic write of current unix timestamp into stamp. Best-effort."""
    import time as _time
    stamp.parent.mkdir(parents=True, exist_ok=True)
    tmp = stamp.with_suffix(stamp.suffix + ".tmp")
    tmp.write_text(f"{_time.time():.6f}")
    os.replace(tmp, stamp)


def _migrate_legacy_spawn_stamp(lore_root: Path, role: str) -> None:
    """Unlink the pre-flock stamp file if present; log to hook-events on failure."""
    old = lore_root / ".lore" / f"last-curator-{role}-spawn"
    try:
        old.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        try:
            HookEventLogger(lore_root).emit(
                event="spawn-throttle",
                outcome="warning",
                error={
                    "type": "LegacyStampMigrationFailed",
                    "message": str(exc),
                    "role": role,
                },
            )
        except Exception:
            pass


def _open_proc_log(lore_root: Path, role: str, *, keep: int = 3) -> int | None:
    """Open .lore/proc/<role>.log for subprocess output, rotating previous generations."""
    import contextlib

    proc_dir = lore_root / ".lore" / "proc"
    try:
        proc_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    log_path = proc_dir / f"{role}.log"
    with contextlib.suppress(OSError):
        (proc_dir / f"{role}.log.{keep}").unlink(missing_ok=True)
    for i in range(keep, 1, -1):
        src = proc_dir / f"{role}.log.{i - 1}"
        dst = proc_dir / f"{role}.log.{i}"
        with contextlib.suppress(OSError):
            os.replace(str(src), str(dst))
    if log_path.exists():
        with contextlib.suppress(OSError):
            os.replace(str(log_path), str(proc_dir / f"{role}.log.1"))
    try:
        return os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    except OSError:
        return None


def _rotate_meta_sidecar(proc_dir: Path, role: str, *, keep: int = 3) -> None:
    """Rotate <role>.meta.json alongside proc logs. Best-effort."""
    import contextlib
    with contextlib.suppress(OSError):
        (proc_dir / f"{role}.meta.json.{keep}").unlink(missing_ok=True)
    for i in range(keep, 1, -1):
        src = proc_dir / f"{role}.meta.json.{i - 1}"
        dst = proc_dir / f"{role}.meta.json.{i}"
        with contextlib.suppress(OSError):
            os.replace(str(src), str(dst))
    current = proc_dir / f"{role}.meta.json"
    if current.exists():
        with contextlib.suppress(OSError):
            os.replace(str(current), str(proc_dir / f"{role}.meta.json.1"))


def _process_is_ours(pid: int) -> bool:
    """True if ``pid`` is alive AND looks like a lore_cli process.

    On Linux the cmdline check via ``/proc/<pid>/cmdline`` dodges PID-recycle
    false positives — the kernel reuses PIDs aggressively, so a bare
    ``os.kill(pid, 0)`` on a stale meta.json could match an unrelated
    process. On non-Linux the cmdline file isn't present and we fall back
    to the liveness probe alone (the rare false positive self-heals once
    the next successful spawn rewrites meta.json).
    """
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if not cmdline_path.exists():
        return True  # non-Linux fallback
    try:
        cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ")
    except OSError:
        return True  # alive but unreadable; assume ours
    return b"lore_cli" in cmdline


def _prior_spawn_runaway(
    lore_root: Path, role: str, *, runaway_age_s: int
) -> dict | None:
    """Return runaway-process info if the prior spawn for ``role`` is hung.

    "Hung" = sidecar meta.json says ``exit_code is None`` AND its recorded
    ``pid`` is still alive on this host AND ``start_ts`` is older than
    ``runaway_age_s`` seconds.

    Returns ``None`` (safe to spawn) when meta.json is absent, malformed,
    already exited, or the prior process is young/dead. The returned dict
    carries ``pid``, ``age_s``, ``start_ts`` for telemetry.

    Final gate before ``_spawn_detached`` commits a fresh subprocess —
    catches the pile-up pattern where a child hangs (e.g. the v0.37.0 lock
    spin) and the cooldown stamp keeps green-lighting new spawns on the
    same broken state. See issue #42 for the related lockfile silent-
    cleanup follow-up.
    """
    import json as _json
    import time as _time

    meta_path = lore_root / ".lore" / "proc" / f"{role}.meta.json"
    try:
        meta = _json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    if meta.get("exit_code") is not None:
        return None
    pid = meta.get("pid")
    start_ts = meta.get("start_ts")
    if not isinstance(pid, int) or not isinstance(start_ts, (int, float)):
        return None
    age_s = _time.time() - start_ts
    if age_s < runaway_age_s:
        return None
    if not _process_is_ours(pid):
        return None
    return {"pid": pid, "age_s": int(age_s), "start_ts": start_ts}


def _spawn_detached(
    lore_root: Path,
    role: str,
    cmd: list[str],
    *,
    cooldown_s: int,
    migrate_stamp: bool = False,
    runaway_age_s: int | None = None,
) -> bool:
    """Fire-and-forget a subprocess under a spawn lock + cooldown stamp.

    Acquires a non-blocking flock on the per-role spawn lock. Returns False
    if another process holds the lock OR the cooldown stamp is still fresh
    OR the prior spawn for this role is still alive past the runaway
    threshold (default ``cooldown_s * 5``).

    The runaway gate is the safety net for "child hangs and cooldown keeps
    green-lighting fresh spawns" — once tripped, a single warning event is
    appended to ``hook-events.jsonl`` per ``cooldown_s * 10`` window
    (throttled via ``curator-<role>.runaway.stamp``) so users running
    ``lore status`` / grepping the log can see the issue without it
    spamming on every UserPromptSubmit.
    """
    import contextlib
    import subprocess
    from lore_core.lockfile import try_acquire_spawn_lock

    effective_runaway = (
        runaway_age_s if runaway_age_s is not None else cooldown_s * 5
    )

    with try_acquire_spawn_lock(lore_root, role) as (held, stamp):
        if not held:
            return False
        if _stamp_within_cooldown(stamp, cooldown_s):
            return False
        runaway = _prior_spawn_runaway(
            lore_root, role, runaway_age_s=effective_runaway
        )
        if runaway is not None:
            warn_stamp = (
                lore_root / ".lore" / f"curator-{role}.runaway.stamp"
            )
            if not _stamp_within_cooldown(warn_stamp, cooldown_s * 10):
                try:
                    HookEventLogger(lore_root).emit(
                        event="spawn-throttle",
                        outcome="prior-runaway",
                        role=role,
                        error={
                            "type": "PriorSpawnAlive",
                            "pid": runaway["pid"],
                            "age_s": runaway["age_s"],
                            "runaway_threshold_s": effective_runaway,
                        },
                    )
                except Exception:
                    pass
                with contextlib.suppress(OSError):
                    _write_stamp(warn_stamp)
            return False
        if migrate_stamp:
            _migrate_legacy_spawn_stamp(lore_root, role)
        env = os.environ.copy()
        # Re-inject as LORE_ROOT so child processes resolve identically
        # without re-reading ~/.config/lore/config.yml. The child's
        # lore_root_source() will report "env" even if the parent
        # resolved via config — that's intentional. Resolution-source
        # provenance is per-process; the path value is what matters
        # across boundaries.
        env["LORE_ROOT"] = str(lore_root)
        env["LORE_CURATOR_MODE"] = "1"
        log_fd = _open_proc_log(lore_root, role)
        proc_dir = lore_root / ".lore" / "proc"
        meta_path = proc_dir / f"{role}.meta.json"
        _rotate_meta_sidecar(proc_dir, role)
        wrapped_cmd = [
            sys.executable, "-m", "lore_cli._proc_wrapper",
            str(meta_path), "--", *cmd,
        ]
        try:
            subprocess.Popen(
                wrapped_cmd,
                cwd=str(lore_root),
                start_new_session=True,
                stdout=log_fd if log_fd is not None else subprocess.DEVNULL,
                stderr=log_fd if log_fd is not None else subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            if log_fd is not None:
                os.close(log_fd)
            return False
        if log_fd is not None:
            os.close(log_fd)
        with contextlib.suppress(OSError):
            _write_stamp(stamp)
        return True


def _spawn_detached_curator_a(lore_root: Path, *, cooldown_s: int = 60) -> bool:
    """Fire-and-forget `lore curator run` subprocess."""
    return _spawn_detached(
        lore_root, "a",
        [sys.executable, "-m", "lore_cli", "curator", "run"],
        cooldown_s=cooldown_s, migrate_stamp=True,
    )


_HANDOVER_RECENT_S = 3600   # 60 min — "the same Claude project recently"
_HANDOVER_POLL_INTERVAL_S = 0.1


def _poll_buffer_handover(
    lore_root: Path,
    cwd: Path,
    *,
    timeout_s: float = 5.0,
) -> list[str]:
    """Wait briefly for in-flight buffer flushes that match this cwd to close.

    Returns a list of context lines to inject into the SessionStart
    output. Each line is either ``> Picked up [[<slug>]]`` for a freshly
    closed buffer or ``> Previous session being synthesised...`` for the
    timeout case.
    """
    import time as _t
    from datetime import UTC, datetime as _dt

    from lore_curator.buffer_store import iter_all

    cwd_str = str(cwd)
    deadline = _t.monotonic() + timeout_s

    candidates: dict[str, dict[str, Any]] = {}
    now = _dt.now(UTC)
    for buf in iter_all(lore_root):
        sidecar = buf.read_sidecar()
        if sidecar is None or sidecar.flush_requested is None:
            continue
        if sidecar.cwd == cwd_str:
            candidates[buf.stem] = {"buf": buf, "matched_via": "cwd"}
            continue
        last = sidecar.last_appended_at
        try:
            ts = _dt.fromisoformat(last.replace("Z", "+00:00")) if last else None
        except ValueError:
            ts = None
        if ts is not None and (now - ts).total_seconds() < _HANDOVER_RECENT_S:
            candidates[buf.stem] = {"buf": buf, "matched_via": "recent"}

    if not candidates:
        return []

    closed_wikilinks: list[str] = []
    pending_stems: set[str] = set(candidates.keys())

    while _t.monotonic() < deadline and pending_stems:
        for stem in list(pending_stems):
            entry = candidates[stem]
            buf = entry["buf"]
            sidecar = buf.read_sidecar()
            # Buffer relocated to _done/ -> read the moved sidecar.
            if sidecar is None:
                done_sidecar = lore_root / ".lore" / "buffers" / "_done" / f"{stem}.state.json"
                if done_sidecar.exists():
                    try:
                        raw = json.loads(done_sidecar.read_text())
                        from lore_curator.buffer_store import Sidecar as _SC
                        sidecar = _SC.from_dict(raw)
                    except (OSError, json.JSONDecodeError, ValueError):
                        sidecar = None
            if sidecar is None:
                pending_stems.discard(stem)
                continue
            if sidecar.state == "closed":
                if sidecar.stub_path:
                    stub = Path(sidecar.stub_path)
                    closed_wikilinks.append(f"[[{stub.stem}]]")
                pending_stems.discard(stem)
        if pending_stems:
            _t.sleep(_HANDOVER_POLL_INTERVAL_S)

    lines: list[str] = []
    for wikilink in closed_wikilinks:
        lines.append(f"> Picked up {wikilink} from a prior session.")
    if pending_stems:
        try:
            HookEventLogger(lore_root).emit(
                event="session-start",
                outcome="flush-handover-timeout",
                cwd=str(cwd),
                pending=sorted(pending_stems),
            )
        except Exception:  # noqa: BLE001
            pass
        lines.append(
            "> Previous session note still being synthesised — it will appear "
            "in a subsequent heartbeat."
        )
    return lines


def _request_flush_for_my_buffers(
    lore_root: Path,
    *,
    trigger: str,
    max_scan: int = 20,
) -> int:
    """Stamp ``flush_requested`` on every live buffer owned by this PID.

    Walks ``.lore/buffers/*.state.json`` (sidecar-only, bounded by
    ``max_scan``); for each match, takes the per-buffer flock and CASes
    ``accumulating -> ready`` while writing ``flush_requested``. Returns
    the count of buffers stamped.

    Buffers already in ``ready`` / ``flushing`` / ``closed`` states are
    untouched — another path (cap-trip, prior session-end) already
    routed them.
    """
    from datetime import UTC, datetime as _datetime

    from lore_curator.buffer_store import (
        BufferTransitionError,
        FlushRequest,
        iter_for_pid,
    )

    stamped = 0
    pid = os.getpid()
    now_iso = _datetime.now(UTC).isoformat().replace("+00:00", "Z")
    scanned = 0
    for buf in iter_for_pid(lore_root, pid):
        scanned += 1
        if scanned > max_scan:
            break
        try:
            with buf.with_lock(blocking=False) as held:
                if not held:
                    continue
                sidecar = buf.read_sidecar()
                if sidecar is None or sidecar.state in ("flushing", "closed"):
                    continue
                if sidecar.flush_requested is not None and sidecar.state == "ready":
                    # Already routed; nothing to do.
                    continue
                req = FlushRequest(trigger=trigger, requested_at=now_iso, by_pid=pid)
                if sidecar.state == "accumulating":
                    try:
                        buf.transition("ready", flush_requested=req)
                    except BufferTransitionError:
                        continue
                else:  # "ready" without flush_requested
                    buf.patch(flush_requested=req)
                stamped += 1
        except OSError:
            continue
    return stamped


def _wiki_should_spawn(
    entries: "list[TranscriptLedgerEntry]",
    wiki_cfg: "WikiConfig",
    *,
    now: "datetime",
) -> "tuple[bool, str]":
    """Decide whether Curator A should spawn for a single wiki's pending bucket.

    Pure-functional — no I/O. Reads only fields cached on the ledger entry
    (``total_turns`` is stamped by ``transcript_sync``). Safe to call from
    every UserPromptSubmit heartbeat without measurable cost.

    Returns ``(spawn, reason)``. The reason is a short string emitted into
    hook telemetry so we can debug "why did/didn't this spawn?" without
    re-deriving the gate inputs.

    OR-gate:
      * sum(total_turns − digested_index_hint) ≥ threshold_pending_turns
      * (now − min(last_mtime)) ≥ max_pending_age_s   — age fallback so old
        work below the turns threshold still files within bounded latency.
    """
    if not entries:
        return False, "empty"
    new_turns = sum(
        max(0, e.total_turns - (e.digested_index_hint or 0))
        for e in entries
    )
    if new_turns >= wiki_cfg.curator.threshold_pending_turns:
        return (
            True,
            f"turns:{new_turns}>={wiki_cfg.curator.threshold_pending_turns}",
        )
    oldest_mtime = min(e.last_mtime for e in entries)
    age_s = int((now - oldest_mtime).total_seconds())
    if age_s >= wiki_cfg.curator.max_pending_age_s:
        return True, f"age:{age_s}s>={wiki_cfg.curator.max_pending_age_s}s"
    return False, f"under(turns={new_turns},age={age_s}s)"


def _now_utc() -> "datetime":
    """Return datetime.now(UTC). Isolated as a seam so tests can pin time."""
    from datetime import UTC, datetime as _dt
    return _dt.now(UTC)


def _curator_c_email() -> str:
    """Resolve git user.email → hostname fallback → empty (offset=0)."""
    from lore_core.git import git_user_email
    return git_user_email(fallback_hostname=True)


def _curator_c_jitter_seconds(email: str) -> int:
    """Deterministic 0-48h offset from SHA-256(email). Empty → 0 (fire at Monday 00Z)."""
    import hashlib
    if not email:
        return 0
    h = hashlib.sha256(email.encode()).hexdigest()[:8]
    return int(h, 16) % 172800  # 48h in seconds


def _iso_week_monday_utc(ts: "datetime") -> "datetime":
    """Monday 00:00Z of the ISO week containing ts."""
    from datetime import datetime as _dt
    from datetime import UTC, timedelta
    weekday = ts.isocalendar().weekday  # 1..7, Monday=1
    date = ts.date() - timedelta(days=weekday - 1)
    return _dt(date.year, date.month, date.day, tzinfo=UTC)


def _spawn_detached_curator_c(
    lore_root: Path, *, cooldown_s: int = 3600
) -> bool:
    """Fire-and-forget `lore curator run --defrag` subprocess (Curator C)."""
    return _spawn_detached(
        lore_root, "c",
        [sys.executable, "-m", "lore_cli", "curator", "run", "--defrag"],
        cooldown_s=cooldown_s,
    )


def _render_drain_lines(lore_root: Path, cwd: Path) -> list[str]:
    """Compile the two drain-banner lines shown at SessionStart.

    Line 1 — "· This session"   — session-scoped notes filed/appended
    Line 2 — "· Since you left" — _system events since the system
                                  cursor last advanced

    Both lines are omitted when their respective stream has no new
    events. Returns an empty list when both are silent (callers
    suppress the newline).

    Cursor advance: each stream owns its own cursor. The session
    cursor (``{sid}.cursor``) prevents repeat SessionStarts within
    one Claude run from re-rendering session events. The system
    cursor (``_system.cursor``) is the single authoritative
    "shown through" mark for the shared system stream — without it,
    a stale row in ``_system.jsonl`` would haunt every fresh session.
    Cold-start initialises ``_system.cursor`` to ``now`` so the first
    read on a new install never reaches back through history.
    """
    from lore_core.drain import SYSTEM_SESSION, DrainStore, resolve_session_id

    sid, _ = resolve_session_id(cwd)
    session_store = DrainStore(lore_root, sid)
    system_store = DrainStore(lore_root, SYSTEM_SESSION)

    session_cursor = session_store.read_cursor()
    session_events = session_store.read(since=session_cursor, limit=200)

    system_cursor = system_store.read_or_init_cursor()
    system_events = system_store.read(since=system_cursor, limit=200)

    lines: list[str] = []
    if session_events:
        counts = _tally_drain(session_events)
        summary = _format_drain_summary(counts, session_events)
        if summary:
            lines.append(f"  · This session   {summary}")

    if system_events:
        counts = _tally_drain(system_events)
        summary = _format_drain_summary(counts, system_events)
        if summary:
            lines.append(f"  · Since you left {summary}")

    # Advance each cursor to ``newest + 1µs`` — `since` in DrainStore.read
    # is inclusive (``ts >= since``), so setting the cursor to the event's
    # own ts would resurface it on the next banner call.
    from datetime import timedelta
    if session_events:
        newest = max(e.ts for e in session_events)
        session_store.write_cursor(newest + timedelta(microseconds=1))
    if system_events:
        newest = max(e.ts for e in system_events)
        system_store.write_cursor(newest + timedelta(microseconds=1))

    return lines


def _tally_drain(events) -> dict[str, int]:
    from collections import Counter
    return dict(Counter(e.event for e in events))


def _latest_wikilink(events, event_name: str) -> str | None:
    """Return the wikilink from the most recent event of the given type."""
    for e in reversed(events):
        if e.event == event_name:
            return e.data.get("wikilink")
    return None


def _format_drain_summary(counts: dict[str, int], events) -> str:
    """Render a short "N notes · M appended · K synced" phrase."""
    parts: list[str] = []
    n_filed = counts.get("note-filed", 0)
    n_appended = counts.get("note-appended", 0)
    n_surface = counts.get("surface-proposed", 0)

    if n_filed:
        wikilink = _latest_wikilink(events, "note-filed")
        if wikilink and n_filed == 1:
            parts.append(f"new note {wikilink}")
        else:
            parts.append(f"{n_filed} new notes")
    if n_appended:
        wikilink = _latest_wikilink(events, "note-appended")
        if wikilink and n_appended == 1:
            parts.append(f"added to {wikilink}")
        else:
            parts.append(f"{n_appended} added")
    if n_surface:
        parts.append(f"{n_surface} surface proposed")
    return " · ".join(parts)


def _spawn_detached_transcript_sync(
    lore_root: Path, *, cooldown_s: int = 300
) -> bool:
    """Fire-and-forget ``lore transcripts sync`` subprocess.

    Runs on the same spawn-lock + cooldown pattern as the curators, so
    a busy SessionStart hook can't stampede the filesystem with parallel
    sync jobs. The P4a sync itself is idempotent; the lock exists purely
    as a politeness budget.
    """
    return _spawn_detached(
        lore_root, "transcripts",
        [sys.executable, "-m", "lore_cli", "transcripts", "sync"],
        cooldown_s=cooldown_s,
    )


def _spawn_detached_curator_b(
    lore_root: Path, wiki_name: str, *, cooldown_s: int = 300
) -> bool:
    """Fire-and-forget `lore curator run --abstract --wiki <name>` subprocess."""
    return _spawn_detached(
        lore_root, "b",
        [sys.executable, "-m", "lore_cli",
         "curator", "run", "--abstract", "--wiki", wiki_name],
        cooldown_s=cooldown_s, migrate_stamp=True,
    )


# ---------------------------------------------------------------------------
# Capture subcommand
# ---------------------------------------------------------------------------


@hook_app.command("capture")
def capture(
    event: str = typer.Option(
        ...,
        help="session-end | pre-compact | session-start",
    ),
    transcript: Path | None = typer.Option(None, help="Explicit transcript path; else autodetect via adapter."),
    cwd_override: Path | None = typer.Option(None, "--cwd", help="Explicit cwd; else CLAUDE_PROJECT_DIR or os.getcwd()."),
    integration: str = typer.Option("claude-code", help="Adapter integration name."),
) -> None:
    """Hot-path capture hook — called by Claude Code on SessionEnd / PreCompact / SessionStart.

    Must return in <100ms. Updates the sidecar ledger; spawns detached
    curator when pending work exceeds threshold. No LLM, no network,
    bounded FS walk (8 levels).
    """
    if _in_curator_mode():
        return
    _read_hook_payload()
    if _session_off_all():
        # `/lore:off all` is the security-first contract: no transcript
        # ingestion, no curator spawn, no ledger writes, no vault output
        # while the user has muted us. SessionEnd is the *only* hook bound
        # to the capture pipeline, so if this is bypassed the user can
        # mute SessionStart/PreCompact/Stop/UserPromptSubmit and still see
        # vault content surface from a curator that ran during their
        # "muted" session.
        return
    import time as _time
    from lore_adapters import UnknownIntegrationError
    from lore_core.hook_log import _ppid_cmd

    start = _time.monotonic()
    cwd = cwd_override or _resolve_cwd_capture()
    _capture_pid = os.getpid()
    _capture_ppid_cmd = _ppid_cmd()

    # Never capture transcripts from the vault root — curator subprocesses
    # run with cwd=LORE_ROOT and their claude -p transcripts must not be
    # re-ingested as user sessions. Only skip when the cwd is the vault root
    # AND has no explicit scope attachment (a real project attached at the
    # vault root would still be captured).
    try:
        _vault = get_lore_root().resolve()
        if Path(cwd).resolve() == _vault and resolve_scope(cwd) is None:
            return
    except OSError:
        # Path resolution failed (broken symlink, permission, etc.); fall
        # through so the calling capture path still runs.
        pass

    scope = resolve_scope(cwd)
    if scope is None:
        # Unattached cwd — no ledger work to do, but we still emit a hook
        # event so "hook fired but declined" is distinguishable from "hook
        # never fired" in `lore status` / `lore runs list --hooks`.
        try:
            HookEventLogger(get_lore_root()).emit(
                event=event, integration=integration, scope=None,
                duration_ms=int((_time.monotonic() - start) * 1000),
                outcome="no-scope",
                cwd=str(cwd),
                pid=_capture_pid,
                ppid_cmd=_capture_ppid_cmd,
            )
        except Exception:
            pass
        return

    lore_root = _infer_lore_root(scope.claude_md_path)
    logger = HookEventLogger(lore_root)
    outcome = "no-new-turns"
    run_id: str | None = None
    pending_after = 0
    pending_by_wiki_counts: dict[str, int] = {}
    scope_payload = {"wiki": scope.wiki, "scope": scope.scope}

    try:
        tledger = TranscriptLedger(lore_root)

        try:
            adapter = get_adapter(integration)
        except UnknownIntegrationError:
            logger.emit(
                event=event, integration=integration, scope=scope_payload,
                duration_ms=int((_time.monotonic() - start) * 1000),
                outcome="error",
                pending_after=0,
                error={"type": "UnknownIntegrationError", "message": integration},
                cwd=str(cwd),
                pid=_capture_pid,
                ppid_cmd=_capture_ppid_cmd,
            )
            raise typer.Exit(code=1)

        _register_pending_transcripts(
            lore_root, cwd, adapter=adapter, transcript=transcript
        )

        # Buffer-and-flush: at session-end / pre-compact, walk this
        # session's live buffers and stamp ``flush_requested`` so the
        # detached curator-A spawn (or a manual ``lore curator flush``)
        # routes them to ``synthesis.flush_buffer``. Bounded sidecar
        # reads keep the hook inside its sub-100ms contract.
        if event in ("session-end", "pre-compact"):
            try:
                _request_flush_for_my_buffers(
                    lore_root, trigger=event, max_scan=20,
                )
            except Exception as exc:  # noqa: BLE001 - hook must never fail on this
                logger.emit(
                    event=event, integration=integration, scope=scope_payload,
                    duration_ms=int((_time.monotonic() - start) * 1000),
                    outcome="warning",
                    error={"type": type(exc).__name__, "message": str(exc)},
                    cwd=str(cwd),
                    pid=_capture_pid,
                    ppid_cmd=_capture_ppid_cmd,
                )

        pending = tledger.pending()
        pending_after = len(pending)
        buckets = tledger.pending_by_wiki()
        # Counts-dict for telemetry (includes __orphan__/__unattached__ buckets).
        pending_by_wiki_counts = {k: len(v) for k, v in buckets.items()}
        cfg = _load_wiki_cfg_from_scope(scope, lore_root)

        # Spawn-gate: turn-aware OR (turns ≥ threshold) (oldest age ≥ fallback).
        # Computed via `_wiki_should_spawn` per wiki bucket. Force-spawn at
        # session-end / pre-compact regardless of gate so no in-flight work
        # is stranded across session boundaries (handover guarantee).
        now = _now_utc()
        crossed: list[str] = []
        spawn_reasons: dict[str, str] = {}
        for wiki_name, entries in buckets.items():
            if wiki_name.startswith("__"):
                continue
            if len(entries) == 0:
                continue
            wiki_cfg = _load_wiki_cfg_for_wiki(lore_root, wiki_name)
            should, reason = _wiki_should_spawn(entries, wiki_cfg, now=now)
            spawn_reasons[wiki_name] = reason
            if should:
                crossed.append(wiki_name)

        force_eos = event in ("session-end", "pre-compact") and pending_after > 0

        if crossed or force_eos:
            spawned = _spawn_detached_curator_a(
                lore_root, cooldown_s=cfg.curator.curator_a_cooldown_s
            )
            if spawned:
                outcome = (
                    "spawned-curator-eos"
                    if (force_eos and not crossed)
                    else "spawned-curator"
                )
            else:
                outcome = "spawn-cooldown"
        elif pending_after > 0:
            outcome = "below-threshold"
        else:
            outcome = "no-new-turns"

    except typer.Exit:
        raise
    except Exception as exc:
        logger.emit(
            event=event, integration=integration, scope=scope_payload,
            duration_ms=int((_time.monotonic() - start) * 1000),
            outcome="error",
            pending_after=pending_after,
            pending_by_wiki=pending_by_wiki_counts,
            error={"type": type(exc).__name__, "message": str(exc)},
            cwd=str(cwd),
            pid=_capture_pid,
            ppid_cmd=_capture_ppid_cmd,
        )
        raise
    else:
        logger.emit(
            event=event, integration=integration, scope=scope_payload,
            duration_ms=int((_time.monotonic() - start) * 1000),
            outcome=outcome,
            pending_after=pending_after,
            pending_by_wiki=pending_by_wiki_counts,
            run_id=run_id,
            cwd=str(cwd),
            pid=_capture_pid,
            ppid_cmd=_capture_ppid_cmd,
        )
        # Write session-end breadcrumb for display at next SessionStart.
        # Only for session-end and pre-compact; session-start is already visible.
        if event in ("session-end", "pre-compact"):
            try:
                from lore_cli.breadcrumb import render_session_end_breadcrumb, write_pending_breadcrumb
                # The current breadcrumb body ignores both pending_after and
                # threshold (only the error branch returns text). Pass
                # threshold_pending_turns as a forward-compatible value if
                # the breadcrumb ever surfaces gate state.
                threshold = 30
                try:
                    threshold = cfg.curator.threshold_pending_turns
                except Exception:
                    pass
                crumb = render_session_end_breadcrumb(
                    outcome=outcome,
                    pending_after=pending_after,
                    threshold=threshold,
                )
                if crumb is not None:
                    write_pending_breadcrumb(lore_root, crumb)
            except Exception:
                pass  # breadcrumb is best-effort, never fatal


@hook_app.command("plan-capture")
def cmd_plan_capture(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """PostToolUse:ExitPlanMode handler — capture an accepted plan to the wiki.

    Stdin payload (Claude Code hook):

    .. code-block:: json

       {
         "tool_input": { "plan": "## …markdown…" },
         "tool_response": { "plan": "…", "isAgent": false,
                            "filePath": "…", "hasTaskTool": true },
         "cwd": "/abs/path",
         "session_id": "…"
       }

    Claude Code only fires ``PostToolUse:ExitPlanMode`` after the user accepts
    the plan; rejection bounces back into plan mode without producing a
    tool_result. The handler therefore treats firing as the authoritative
    approval signal and does not gate on a ``tool_response.approved`` field
    (which the harness does not send).

    Behaviour:

    * Unattached cwd → soft hint to ``/lore:attach``; exit 0 (don't crash the harness).
    * Top-level exception → orphan-dump payload to ``~/.cache/lore/orphan-plans/``
      and emit a recovery hint. **Never silent loss** (differs from SessionStart's
      silent-fail policy because plan loss is unrecoverable).
    """
    from datetime import date as _date

    if _in_curator_mode():
        return
    cwd_resolved = Path(_resolve_cwd(cwd))
    lore_root_for_log = _infer_lore_root(cwd_resolved) or Path.home()
    logger = HookEventLogger(lore_root_for_log)

    raw_payload: bytes | None = None
    try:
        from lore_core.io import read_hook_stdin
        from lore_core.plans.ingest import IngestSource, ingest_plan
        from lore_core.plans.writer import (
            compute_source_hash,
            plan_path,
            write_plan_note,
        )

        stdin_result = read_hook_stdin()
        if stdin_result.outcome != "ok":
            logger.emit(
                event="plan-capture",
                outcome=stdin_result.outcome,
                cwd=str(cwd_resolved),
            )
            if stdin_result.outcome == "tty":
                _emit_post_tool_use(
                    "lore: plan-capture reads JSON from stdin (run via Claude Code)",
                    plain=plain,
                )
            return

        raw_payload = stdin_result.data
        try:
            payload = json.loads(raw_payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            logger.emit(
                event="plan-capture",
                outcome="malformed-json",
                error={"type": "JSONDecodeError", "message": str(e)},
                cwd=str(cwd_resolved),
            )
            _orphan_dump(raw_payload, plain=plain)
            return

        scope = resolve_scope(cwd_resolved)
        if scope is None:
            logger.emit(
                event="plan-capture",
                outcome="unattached",
                cwd=str(cwd_resolved),
            )
            _emit_post_tool_use(
                "lore: plan ignored (cwd not attached to a wiki — `/lore:attach`)",
                plain=plain,
            )
            return

        wiki_root = get_wiki_root() / scope.wiki
        repo_slug = current_repo(cwd_resolved)

        # Route the entire hook payload through the ingest dispatcher's
        # ``hook_payload`` branch — this exercises the producer-keyed
        # adapter (Claude Code today; Cursor / Aider tomorrow) and
        # returns a unified confidence verdict + structured warnings.
        # The branch handles both "no plan markdown extractable" and
        # "extractable but unstructured" via different warning codes.
        ingest_result = ingest_plan(
            IngestSource(
                kind="hook_payload", payload=payload, producer="claude-code"
            )
        )
        plan = ingest_result.plan
        warning_codes = [w.code for w in ingest_result.warnings]
        warning_messages = [w.message for w in ingest_result.warnings]
        # ``adapter_name`` is ``hook/claude-code:tool_input.plan`` etc;
        # extract the source field for legacy telemetry compat.
        adapter_name = ingest_result.adapter_name
        source_field = (
            adapter_name.split(":", 1)[1]
            if ":" in adapter_name and adapter_name.startswith("hook/")
            else adapter_name
        )

        # No plan markdown found in the payload at all → orphan dump.
        if "payload_no_plan" in warning_codes:
            logger.emit(
                event="plan-capture",
                outcome="no-plan-in-payload",
                source_field=source_field,
                cwd=str(cwd_resolved),
            )
            _orphan_dump(raw_payload, plain=plain)
            return

        # Markdown extracted but the classifier couldn't recognize a
        # step structure → fail loud, do NOT file.
        if ingest_result.confidence == "fallback":
            logger.emit(
                event="plan-capture",
                outcome="unstructured",
                source_field=source_field,
                shape_diagnosis=warning_messages[0] if warning_messages else "",
                warning_codes=warning_codes,
                slug=plan.slug,
                cwd=str(cwd_resolved),
            )
            _emit_post_tool_use(
                _format_unstructured_message(
                    slug=plan.slug,
                    warning_codes=warning_codes,
                    warning_messages=warning_messages,
                ),
                plain=plain,
            )
            return  # do NOT write the plan

        # Recompute source_hash from the extracted markdown for dedup
        # parity with prior behavior (the writer compares this hash
        # against existing files to decide filed/deduped/updated).
        from lore_core.plans.parser import parse_payload as _legacy_extract

        _extracted_text, _ = _legacy_extract(payload)
        source_hash = compute_source_hash(_extracted_text or "")

        target_path = plan_path(wiki_root, plan.slug)
        prior_last_reviewed = ""
        if target_path.exists():
            prior_fm = parse_frontmatter(target_path.read_text())
            prior_last_reviewed = str(prior_fm.get("last_reviewed") or "")

        result = write_plan_note(
            wiki_root=wiki_root,
            plan=plan,
            source_hash=source_hash,
            source_adapter="claude-code-hook",
            repo=repo_slug,
        )

        logger.emit(
            event="plan-capture",
            outcome=result.outcome,
            source_field=source_field,
            mode=plan.mode,
            slug=result.slug,
            cwd=str(cwd_resolved),
        )

        message = _format_capture_message(
            result=result,
            today_iso=_date.today().isoformat(),
            prior_last_reviewed=prior_last_reviewed,
        )
        if message:
            _emit_post_tool_use(message, plain=plain)

    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 — never silent loss for plan capture
        try:
            logger.emit(
                event="plan-capture",
                outcome="error",
                error={"type": type(exc).__name__, "message": str(exc)},
                cwd=str(cwd_resolved),
            )
        except Exception:
            pass
        if raw_payload is not None:
            _orphan_dump(raw_payload, plain=plain, error=str(exc))
        else:
            _emit_post_tool_use(
                f"lore: plan-capture failed ({type(exc).__name__}); see lore status",
                plain=plain,
            )


def _format_unstructured_message(
    *,
    slug: str,
    warning_codes: list[str],
    warning_messages: list[str],
) -> str:
    """User-facing message when the hook refuses to file an unstructured plan.

    The message must give the agent enough context to re-author the plan
    using a recognized shape. We name the canonical example so the
    next attempt has a concrete target.
    """
    code = warning_codes[0] if warning_codes else "shape_unknown"
    detail = warning_messages[0] if warning_messages else "no recognized step structure"
    return (
        f"lore: plan ingest failed ({code}) — {detail}. "
        f"Re-run plan mode with explicit step headings "
        f"(e.g. `### step-1: title`, `### Phase 1 — title`, or hierarchical "
        f"`## Phase N` + `### N.M`). Plan NOT filed."
    )


_HOOK_MSG_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _scrub_systemMessage(text: str) -> str:
    """Make ``text`` safe to embed in a Claude Code hook ``systemMessage``.

    LLM-produced plan titles may contain ANSI escape sequences or other
    control characters; the JSON envelope itself stays valid but the
    downstream parser/UI may render them weirdly. Strip all C0 control
    chars except tab (\\x09) and newline (\\x0a), then take the first
    line and cap at 200 chars.
    """
    line = _first_line(text)
    line = _HOOK_MSG_CONTROL_CHARS.sub("", line)
    return line[:200]


def _emit_post_tool_use(text: str, *, plain: bool) -> None:
    """Emit a JSON envelope for PostToolUse — only ``systemMessage`` is allowed.

    Distinct from ``_emit`` (which handles SessionStart's
    ``hookSpecificOutput`` shape) because PostToolUse hooks are
    constrained to the simpler schema.
    """
    text = (text or "").strip()
    if plain or not text:
        if text:
            print(text)
        return
    print(json.dumps({"systemMessage": _scrub_systemMessage(text)}))


def _orphan_dump(raw_payload: bytes, *, plain: bool, error: str | None = None) -> None:
    """Write the raw payload to ``~/.cache/lore/orphan-plans/<ts>.json`` and notify."""
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    cache_dir = Path.home() / ".cache" / "lore" / "orphan-plans"
    ts = _dt.now(_UTC).strftime("%Y%m%dT%H%M%SZ")
    target = cache_dir / f"{ts}.json"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw_payload)
        msg = (
            f"lore: plan-capture failed; payload at {target}; "
            f"recover: lore plan import --from-orphan {target}"
        )
    except OSError as e:
        msg = (
            f"lore: plan-capture failed and orphan-dump also failed ({e}); "
            "see lore status for the original payload bytes"
        )
    if error:
        msg += f" — error: {error}"
    _emit_post_tool_use(msg, plain=plain)


def _format_capture_message(
    *, result, today_iso: str, prior_last_reviewed: str
) -> str:
    """Pick the user-facing systemMessage based on outcome + within-day rule."""
    slug = result.slug
    n = result.step_count
    if result.outcome == "filed":
        return f"lore: filed [[plan/{slug}]] · {n} steps"
    if result.outcome == "deduped":
        return ""  # silent — exact re-acceptance is uninteresting
    if result.outcome == "updated":
        if prior_last_reviewed == today_iso:
            return f"lore: updated [[plan/{slug}]] · {n} steps"
        return (
            f"lore: updated [[plan/{slug}]] (refreshed body; "
            "preserved status/tags)"
        )
    if result.outcome == "collision-suffixed":
        return f"lore: filed [[plan/{slug}]] (slug collision; {n} steps)"
    return ""


@hook_app.command("plan-edit-writeback")
def cmd_plan_edit_writeback(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
) -> None:
    """PostToolUse:Edit/Write handler — auto-flip pending → in_progress.

    Reads the just-edited file path from the hook payload, intersects it
    with each active plan's ``step_files``, and flips matching ``pending``
    steps to ``in_progress``. Idempotent — already-in_progress and
    already-done steps are not touched.

    Best-effort: any exception is swallowed so a flaky filesystem,
    git failure, or attachments hiccup can't break Edit/Write tool use.
    Emits no systemMessage on success — the flip is invisible by design;
    SessionStart's Resume block surfaces the new state next time.
    """
    if _in_curator_mode():
        return
    try:
        from lore_core.git import git_repo_root
        from lore_core.io import read_hook_stdin
        from lore_core.plans.registry import list_active
        from lore_core.plans.step_status import set_step
        from lore_core.plans.types import StepStatus

        cwd_path = Path(_resolve_cwd(cwd))

        scope = resolve_scope(cwd_path)
        if scope is None:
            return  # unattached cwd — silent no-op
        wiki_root = get_wiki_root() / scope.wiki
        if not wiki_root.exists():
            return

        repo_slug = current_repo(cwd_path)
        if repo_slug is None:
            return  # not in a git repo
        repo_root = git_repo_root(cwd_path)
        if repo_root is None:
            return

        stdin_result = read_hook_stdin()
        if stdin_result.outcome != "ok":
            return
        try:
            payload = json.loads(stdin_result.data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return

        tool_input = payload.get("tool_input") or {}
        file_path = tool_input.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return

        # Normalize to a path relative to repo root for matching against
        # step_files entries (which the LLM authors as repo-relative).
        # Fall back to the raw input when relativization isn't possible
        # (e.g. file outside repo, or already-relative input).
        rel_candidates: set[str] = set()
        try:
            abs_path = Path(file_path)
            if abs_path.is_absolute():
                rel = abs_path.resolve().relative_to(repo_root.resolve())
                rel_candidates.add(str(rel))
            else:
                rel_candidates.add(file_path)
                rel_candidates.add(str(Path(file_path)))
        except (ValueError, OSError):
            rel_candidates.add(file_path)

        cards = list_active(wiki_root, repo=repo_slug)
        if not cards:
            return

        for card in cards:
            for step_id, files in card.step_files.items():
                if not files:
                    continue
                if not any(f in rel_candidates for f in files):
                    continue
                # Only flip pending → in_progress. Existing entries
                # (in_progress, done, blocked) are left alone.
                if step_id in card.step_status:
                    continue
                try:
                    set_step(
                        wiki_root=wiki_root,
                        slug=card.slug,
                        step_id=step_id,
                        status=StepStatus.IN_PROGRESS,
                    )
                except (FileNotFoundError, ValueError, OSError):
                    # Plan vanished or step ID typo — never break the
                    # editing tool.
                    continue
    except Exception:  # noqa: BLE001 — never break PostToolUse hooks
        return


main = argv_main(hook_app)


if __name__ == "__main__":
    sys.exit(main())
