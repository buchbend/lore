"""Claude Code hook helpers — cheap, deterministic context injection.

These commands read cached files the linter regenerates (_index.md,
_catalog.json) and emit bounded context blobs for the hook stream.
No LLM invocation, no network — the design goal is "as fast as the
filesystem allows after Python startup."

Measured cost on a populated single-wiki vault (Phase 7 audit
2026-04-26):

  - ``lore --help``                — ~600ms (Python startup + typer
                                    dispatch + eager import of ~30
                                    cmd modules in `__main__.py`)
  - ``lore hook session-start --probe``
                                    — ~2.3s end-to-end (the 600ms
                                    startup + ~1.7s of file I/O:
                                    catalog/index reads, scope
                                    resolution, GH calls when
                                    available)

The work *inside* the hook handlers is fast (~50-200ms); Python
startup + the eager-import surface dominate. Lazy-mounting subcommand
typer apps in ``__main__.py`` would cut ~300-500ms but is a
structural refactor (deferred from Phase 7's safe-and-useful scope).
A sub-100ms budget is not realistic with the current dispatcher
shape and was an aspirational target, not a contract.

    lore hook session-start [--cwd PATH]
    lore hook pre-compact  [--cwd PATH]
    lore hook stop

Exposed via `lore_cli.__main__` dispatch (see subcommand wiring there).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

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

# Keep auto-injected context bounded. ~500 tokens ≈ ~2000 characters for
# prose; we cap at 2000 to stay tight.
MAX_CONTEXT_CHARS = 2000
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
    """Return the wiki's _index.md, truncated to fit."""
    index_path = wiki / "_index.md"
    if not index_path.exists():
        return ""
    text = index_path.read_text(errors="replace")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + "\n... (truncated — run /lore:context for full)"


# Matches "## Open items" section up to next `##` or EOF.
_OPEN_ITEMS_RE = re.compile(r"##\s+Open items\s*\n(.+?)(?=\n##|\Z)", re.DOTALL)


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


def _last_session_hint(wiki: Path, max_notes: int = 2) -> list[tuple[str, str]]:
    """Return (slug, summary) pairs for the most recent session notes.

    Reads only YAML frontmatter (first ~1KB). Does not filter by user —
    any user's sessions are shown for cross-user awareness.
    """
    from lore_core.schema import parse_frontmatter

    sessions_dir = wiki / "sessions"
    if not sessions_dir.is_dir():
        return []
    candidates = sorted(sessions_dir.glob("*.md"), reverse=True)
    results: list[tuple[str, str]] = []
    for path in candidates:
        if len(results) >= max_notes:
            break
        try:
            head = path.read_text(errors="replace")[:1024]
        except OSError:
            continue
        fm = parse_frontmatter(head)
        desc = fm.get("summary") or fm.get("description")
        if not desc:
            continue
        results.append((path.stem, desc))
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

    for md in sorted(sessions_dir.glob("*.md"), reverse=True):
        try:
            iso = md.stem[:10]
            d = date.fromisoformat(iso)
        except (ValueError, IndexError):
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


def _gh_issues(repo: str, filter_str: str) -> list[dict]:
    return _run_gh("issue", repo, _gh_mod.split_filter(filter_str))


def _gh_prs(repo: str, filter_str: str) -> list[dict]:
    return _run_gh("pr", repo, _gh_mod.split_filter(filter_str))


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
        issues = _gh_issues(repo, issues_filter)
        prs = _gh_prs(repo, prs_filter)
        if scope:
            scopes = _load_scopes_yml(wiki)
            siblings = _subtree_siblings(scopes, scope)
            parts = scope.split(":")
            subtree_scope = ":".join(parts[:-1]) if len(parts) > 1 else ""
            for _sib_scope, sib_repo in siblings:
                if sib_repo == repo:
                    continue
                subtree_issues += len(_gh_issues(sib_repo, issues_filter))

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
    if project_entry is not None:
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
        hint = os.environ.get("LORE_ROOT") or "(unset, defaulting to ~/lore)"
        return (
            f"lore: no vault at LORE_ROOT={hint}. "
            "Set LORE_ROOT to your vault path or run `lore init`."
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
        parts.append(f"## Open items{' (this repo)' if repo else ''}")
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
            f"No open items for this repo. "
            f"{elsewhere} open items elsewhere in {wiki.name} — `/lore:resume` to see."
        )
        parts.append("")

    # Directive last: see _session_start_from_lore for rationale.
    parts.extend(_load_directive_lines())
    parts.extend(_citation_directive_lines())

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


def _hook_failure_banner(hook_event: str, exc: BaseException) -> str:
    """Build a user-friendly diagnostic for a crashed hook.

    Claude Code surfaces stderr + traceback as
    "Failed with non-blocking status code" when a hook exits non-zero.
    That's noise without a next step. The shield catches the exception,
    feeds the banner through `_emit` like normal, and exits 0 — the user
    sees an actionable message instead of a traceback.

    Common causes named explicitly: stale install (templates / package
    data missing under pipx wheels) and binary-vs-plugin-cache drift.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc) or "(no message)"
    # Truncate noisy paths/values so the banner stays readable.
    if len(exc_msg) > 200:
        exc_msg = exc_msg[:197] + "..."
    return (
        f"⚠ lore {hook_event} hook failed: {exc_name}: {exc_msg}\n"
        "\n"
        "Likely causes + fixes:\n"
        "  • Stale install (e.g. templates not bundled): "
        "[bold]lore install --upgrade[/bold] (or re-run install.sh).\n"
        "  • Binary vs plugin-cache drift: "
        "[bold]lore doctor[/bold] flags it and prints the exact command.\n"
        "  • If the error persists, file an issue: "
        "https://github.com/buchbend/lore/issues\n"
        "\n"
        "Lore continues without its SessionStart banner — your session "
        "is otherwise unaffected."
    )


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
                banner = _hook_failure_banner(typer_event, exc)
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
    """Resolve CWD: explicit --cwd → $CLAUDE_PROJECT_DIR → os.getcwd()."""
    return explicit or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


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
                # left." Session-scoped cursor prevents the same event from
                # showing up on repeat SessionStarts within one Claude run.
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

    _emit("SessionStart", out, plain=plain)


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
    _read_hook_payload()
    if _session_off_all():
        return
    out = _stop()
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

    sys_cursor_path = drain_dir / f"heartbeat-{effective_pid}.cursor"
    sess_cursor_path = drain_dir / f"heartbeat-session-{effective_pid}.cursor"

    sys_cursor_ts = _read_cursor(sys_cursor_path)
    sess_cursor_ts = _read_cursor(sess_cursor_path)

    system_store = DrainStore(lore_root, SYSTEM_SESSION)
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
        _write_cursor(sys_cursor_path, max(e.ts for e in system_events))
    if session_events:
        _write_cursor(sess_cursor_path, max(e.ts for e in session_events))

    _write_stamp(stamp)
    return sys_msg, ctx


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

    sys_msg, ctx = _heartbeat(lore_root, cwd_resolved, wiki_cfg)

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
    """Resolve CWD for capture: $CLAUDE_PROJECT_DIR → os.getcwd()."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(os.getcwd())


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


def _infer_lore_root(claude_md_path: Path) -> Path:
    """Infer LORE_ROOT from env, else walk up from claude_md_path for a wiki/ dir.

    Preference: $LORE_ROOT env var. Otherwise walk up looking for a directory
    that contains a `wiki/` subdirectory — that's the lore_root. Falls back
    to the CLAUDE.md's parent directory.
    """
    env = os.environ.get("LORE_ROOT")
    if env:
        return Path(env)
    for parent in [claude_md_path.parent, *claude_md_path.parents]:
        if (parent / "wiki").is_dir():
            return parent
    # Fallback — the CLAUDE.md's parent (best effort).
    return claude_md_path.parent


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
    lore_root_env = os.environ.get("LORE_ROOT")
    if not lore_root_env:
        return None
    lore_root = Path(lore_root_env)

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


def _spawn_detached(
    lore_root: Path,
    role: str,
    cmd: list[str],
    *,
    cooldown_s: int,
    migrate_stamp: bool = False,
) -> bool:
    """Fire-and-forget a subprocess under a spawn lock + cooldown stamp.

    Acquires a non-blocking flock on the per-role spawn lock. Returns False
    if another process holds the lock OR the cooldown stamp is still fresh.
    """
    import contextlib
    import subprocess
    from lore_core.lockfile import try_acquire_spawn_lock

    with try_acquire_spawn_lock(lore_root, role) as (held, stamp):
        if not held:
            return False
        if _stamp_within_cooldown(stamp, cooldown_s):
            return False
        if migrate_stamp:
            _migrate_legacy_spawn_stamp(lore_root, role)
        env = os.environ.copy()
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
    Line 2 — "· Since you left" — _system events since this session
                                  last rendered a banner

    Both lines are omitted when their respective stream has no new
    events. Returns an empty list when both are silent (callers
    suppress the newline).

    Cursor advance: the session drain's cursor is bumped to the newest
    ts rendered so a second SessionStart inside the same Claude session
    (e.g. re-opening a window) doesn't re-surface the same events.
    """
    from lore_core.drain import SYSTEM_SESSION, DrainStore, resolve_session_id

    sid, _ = resolve_session_id(cwd)
    session_store = DrainStore(lore_root, sid)
    system_store = DrainStore(lore_root, SYSTEM_SESSION)

    # Session cursor = "what have I already shown this session?"
    session_cursor = session_store.read_cursor()
    session_events = session_store.read(since=session_cursor, limit=200)

    # System cursor per-session so repeat SessionStarts in the same
    # Claude run don't spam; we piggyback on the session drain's cursor
    # (events from both streams are only surfaced once per session_cursor
    # advance). This is the simplest model that also handles the "user
    # opens two windows at once" case sanely.
    system_events = system_store.read(since=session_cursor, limit=200)

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

    # Advance cursor to ``newest + 1µs`` — `since` in DrainStore.read is
    # inclusive (``ts >= since``), so setting the cursor to the event's
    # own ts would resurface it on the next banner call.
    all_events = session_events + system_events
    if all_events:
        from datetime import timedelta
        newest = max(e.ts for e in all_events)
        session_store.write_cursor(newest + timedelta(microseconds=1))

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

        if transcript is not None:
            handles = [h for h in adapter.list_transcripts(cwd) if h.path == transcript]
        else:
            handles = adapter.list_transcripts(cwd)

        # Collect new + mtime-changed entries into a single bulk_upsert so
        # the 180KB+ ledger is serialised once per hook, not once per
        # transcript. Keeps the capture path well under its <500ms budget.
        #
        # Attach-time watermark: transcripts older than the attachment's
        # attached_at are pre-stamped as already seen so only future
        # sessions are pending. Use `lore backfill` to opt in to history.
        from lore_core.state.attachments import AttachmentsFile
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

        pending = tledger.pending()
        pending_after = len(pending)
        buckets = tledger.pending_by_wiki()
        # Counts-dict for telemetry (includes __orphan__/__unattached__ buckets).
        pending_by_wiki_counts = {k: len(v) for k, v in buckets.items()}
        cfg = _load_wiki_cfg_from_scope(scope, lore_root)

        # Spawn when any *attached* wiki crosses its own threshold_pending.
        # The `len > 0` clause guards threshold_pending=0 + empty-wiki: the
        # bucket wouldn't be in `buckets` at all, but an explicit guard keeps
        # the intent obvious if the dict later gains zero-count entries.
        crossed: list[str] = []
        for wiki_name, entries in buckets.items():
            if wiki_name.startswith("__"):
                continue
            if len(entries) == 0:
                continue
            wiki_cfg = _load_wiki_cfg_for_wiki(lore_root, wiki_name)
            if len(entries) >= wiki_cfg.curator.threshold_pending:
                crossed.append(wiki_name)

        if crossed:
            spawned = _spawn_detached_curator_a(
                lore_root, cooldown_s=cfg.curator.curator_a_cooldown_s
            )
            outcome = "spawned-curator" if spawned else "spawn-cooldown"
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
                threshold = 3
                try:
                    threshold = cfg.curator.threshold_pending
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
         "tool_response": { "approved": true | false },
         "cwd": "/abs/path",
         "session_id": "…"
       }

    Behaviour:

    * Rejected plan → exit 0 silently (rejection = revision cycle).
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
        from lore_core.plans.parser import parse, parse_payload
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

        approved = bool((payload.get("tool_response") or {}).get("approved"))
        if not approved:
            logger.emit(
                event="plan-capture",
                outcome="rejected",
                cwd=str(cwd_resolved),
            )
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

        plan_text, source_field = parse_payload(payload)
        if plan_text is None:
            logger.emit(
                event="plan-capture",
                outcome="no-plan-in-payload",
                source_field=source_field,
                cwd=str(cwd_resolved),
            )
            _orphan_dump(raw_payload, plain=plain)
            return

        plan = parse(plan_text)
        source_hash = compute_source_hash(plan_text)

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


main = argv_main(hook_app)


if __name__ == "__main__":
    sys.exit(main())
