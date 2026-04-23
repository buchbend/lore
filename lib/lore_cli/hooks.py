"""Claude Code hook helpers — cheap, deterministic context injection.

These commands read cached files the linter regenerates (_index.md,
_catalog.json) and emit bounded context blobs for the hook stream.
No LLM invocation. Each command is designed to be fast (<100ms) and
safe to run on every session.

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
from lore_core.config import get_wiki_root
from lore_core.git import current_repo
from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter
from lore_core.scopes import (
    load_scopes_yml,
    subtree_siblings,
    walk_scope_leaves,
)



# SessionStart writes its injected context to a cache file so /lore:loaded
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
    """Pre-PID-keying cache path; kept as a last-resort fallback."""
    return _cache_dir() / "last-session-start.md"


def _pid_alive(pid: int) -> bool:
    """True if /proc/<pid> exists. Linux-only; returns True elsewhere to be conservative."""
    if not Path("/proc").is_dir():
        return True
    return Path(f"/proc/{pid}").exists()


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
# The canonical content lives in `templates/host-rules/default.md` so
# the same source feeds both this hook (Claude Code) and the Cursor
# installer's `~/.cursor/rules/lore.md`. Module-level `__getattr__`
# below preserves the historical `LORE_DIRECTIVE_LINES` name without
# reading the template at import time (so pytest can monkeypatch the
# template path without import-order pain).
_DIRECTIVE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "templates"
    / "host-rules"
    / "default.md"
)


def _load_directive_lines() -> list[str]:
    """Read the canonical vault-first directive and return as a list.

    Output shape preserves the historical 3-element layout exactly:
    `["## Directives", "- **Vault first.** …", ""]`. The trailing
    empty string produces the blank line spacer in the joined output.
    """
    text = _DIRECTIVE_PATH.read_text()
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
    except Exception:
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
    return text[: max_chars - 40] + "\n... (truncated — run /lore:loaded for full)"


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


def _last_session_hint(wiki: Path, max_notes: int = 2) -> list[str]:
    """Return compact breadcrumbs for the most recent session notes.

    Reads only YAML frontmatter (first ~1KB). Does not filter by user —
    any user's sessions are shown for cross-user awareness.
    """
    from lore_core.schema import parse_frontmatter

    sessions_dir = wiki / "sessions"
    if not sessions_dir.is_dir():
        return []
    candidates = sorted(sessions_dir.glob("*.md"), reverse=True)
    lines: list[str] = []
    for path in candidates:
        if len(lines) >= max_notes:
            break
        try:
            head = path.read_text(errors="replace")[:1024]
        except OSError:
            continue
        fm = parse_frontmatter(head)
        desc = fm.get("summary") or fm.get("description")
        if not desc:
            continue
        slug = path.stem
        lines.append(f"Last: [[{slug}]] — {desc}")
    return lines


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


def _split_filter(raw: str | None) -> list[str]:
    return _gh_mod.split_filter(raw)


def _run_gh(kind: str, repo: str, filter_args: list[str]) -> list[dict]:
    return _gh_mod.run_gh(kind, repo, filter_args)


def _gh_issues(repo: str, filter_str: str) -> list[dict]:
    return _run_gh("issue", repo, _split_filter(filter_str))


def _gh_prs(repo: str, filter_str: str) -> list[dict]:
    return _run_gh("pr", repo, _split_filter(filter_str))


def _format_issue_line(issue: dict) -> str:
    return _gh_mod.format_issue_line(issue)


def _format_pr_line(pr: dict) -> str:
    return _gh_mod.format_pr_line(pr)


# ---------------------------------------------------------------------------
# Session-start hook
# ---------------------------------------------------------------------------


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

    catalog = _wiki_catalog(wiki) or {}
    note_count = catalog.get("stats", {}).get("total_notes", "?")
    stale = _stale_count(wiki)

    scope_label = scope or wiki_name
    status_bits: list[str] = [f"{note_count} notes"]
    if issues:
        status_bits.append(f"{len(issues)} issue{'s' if len(issues) != 1 else ''}")
    if prs:
        status_bits.append(f"{len(prs)} PR{'s' if len(prs) != 1 else ''}")
    if stale:
        status_bits.append(f"{stale} stale")
    status_line = f"lore: loaded {scope_label} ({', '.join(status_bits)}) · /lore:loaded"

    out_parts: list[str] = [status_line, ""]
    out_parts.extend(_load_directive_lines())

    project_entry = _project_note_for_repo(wiki, repo) if repo else None
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

    session_hints = _last_session_hint(wiki)
    if session_hints:
        out_parts.extend(session_hints)
        out_parts.append("")

    if issues:
        header = f"## Open issues ({scope})" if scope else "## Open issues"
        out_parts.append(header)
        for issue in issues[:MAX_ISSUES_INLINE]:
            out_parts.append(_format_issue_line(issue))
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
            out_parts.append(_format_pr_line(pr))
        if len(prs) > MAX_PRS_INLINE:
            out_parts.append(f"- … +{len(prs) - MAX_PRS_INLINE} more")
        out_parts.append("")

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

    # Core stats
    catalog = _wiki_catalog(wiki) or {}
    stats = catalog.get("stats", {})
    note_count = stats.get("total_notes", "?")
    stale = _stale_count(wiki)

    # Repo-scoped open items (repo or None for wiki-wide)
    items, elsewhere = _recent_open_items(wiki, repo=repo)

    # Project note focused on this repo, if any
    project_entry = _project_note_for_repo(wiki, repo) if repo else None

    # One-liner status — repo-scoped when we can
    scope_label = wiki.name if project_entry is None else f"{wiki.name}:{project_entry['name']}"
    stale_tag = f", {stale} stale" if stale else ""
    status_line = (
        f"lore: loaded {scope_label} ({note_count} notes, "
        f"{len(items)} open{stale_tag}) · /lore:loaded"
    )

    parts: list[str] = [status_line, ""]
    parts.extend(_load_directive_lines())

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

    session_hints = _last_session_hint(wiki)
    if session_hints:
        parts.extend(session_hints)
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
# `lore hook why` — read-only cache lookup for the /lore:loaded skill
# ---------------------------------------------------------------------------


def _render_live_state(cwd: Path | None = None) -> str:
    """Render the live-state section for /lore:loaded.

    Uses the same CaptureState that `lore status` consumes, rendered via
    status_cmd's helpers so the output shape matches. On failure returns
    a one-line error so /lore:loaded never crashes on cache rendering.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    try:
        from lore_core import capture_state as _cs_mod
        from lore_core.config import get_lore_root
        from lore_cli import status_cmd

        lore_root = get_lore_root()
        now = _dt.now(_UTC)
        state = _cs_mod.query_capture_state(
            lore_root,
            cwd=Path(_resolve_cwd_capture()) if cwd is None else cwd,
            now=now,
        )
    except Exception as exc:
        return f"(live state unavailable: {type(exc).__name__}: {exc})"

    lines: list[str] = []
    if not state.scope_attached:
        lines.append("(not attached to a wiki — run /lore:attach)")
    else:
        lines.append(f"scope: {state.scope_name}")
        for glyph, msg in [
            status_cmd._render_last_note(state, now),
            status_cmd._render_last_run(state, now),
            status_cmd._render_pending(state),
            status_cmd._render_lock(state),
        ]:
            lines.append(f"  {glyph} {msg}")
    return "\n".join(lines)


def _live_state() -> str:
    """Return live state + the SessionStart cache for the current session.

    Output shape (post-Task-13):

        ── Live state (as of now) ────
        <rendered CaptureState>

        ── Injected at SessionStart ────
        <cached hook body>

    Live state comes first per UX review: a Claude session opening
    ``/lore:loaded`` wants "what's true now" before "what was injected."

    Cache resolution order (unchanged):
      1. ``$LORE_CACHE/sessions/<claude_code_pid>.md``
      2. ``$LORE_CACHE/last-session-start.md`` (legacy, flagged)
      3. An explanatory error if nothing is cached.
    """
    live = _render_live_state()

    # Resolve cached body.
    cached_body: str | None = None
    cc_pid = _claude_code_pid()
    if cc_pid is not None:
        primary = _cache_path_for_pid(cc_pid)
        if primary.exists():
            try:
                cached_body = primary.read_text(errors="replace")
            except OSError:
                cached_body = None

    if cached_body is None:
        legacy = _legacy_cache_path()
        if legacy.exists():
            try:
                body = legacy.read_text(errors="replace")
            except OSError:
                body = ""
            if body:
                cached_body = (
                    "_(read from legacy singleton cache — may be from a "
                    "different concurrent Claude session)_\n\n"
                ) + body

    if cached_body is None:
        cached_body = (
            "lore: no SessionStart cache found. Either the hook has not "
            "fired yet in this session, or hooks are disabled. Check "
            "`~/.claude/settings.json` for a SessionStart entry invoking "
            "`lore hook session-start`, or re-run the installer with "
            "`--with-hooks`.\n"
        )

    return (
        "── Live state (as of now) ────\n"
        f"{live}\n"
        "\n"
        "── Injected at SessionStart ────\n"
        f"{cached_body}"
    )


# ---------------------------------------------------------------------------
# Stop hook (timeout-style prompt)
# ---------------------------------------------------------------------------


def _stop() -> str:
    """Emit a terse reminder to capture a session note.

    True timeout / Esc-to-skip behaviour requires terminal interactivity
    and isn't reliable in non-TTY hook contexts. We emit an agent-
    readable hint so the model can offer to run `/lore:session` on exit
    — the user can ignore it.
    """
    return "lore: consider `/lore:session` to capture this session.\n"


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

    `--plain` dumps raw text to stdout — used by the /lore:loaded skill and
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
        # Cache the injected body so `/lore:loaded` can surface it back to
        # the user. Key by the Claude Code PID so two concurrent
        # sessions don't stomp each other. We walk process ancestry
        # rather than trusting os.getppid() directly: today Claude Code
        # spawns hooks without a shell wrapper (PPID == Claude Code),
        # but the walker keeps us correct if that ever changes. Fall
        # back to PPID if the walker can't resolve (e.g. non-Linux).
        # Keep writing the legacy singleton path too so older skill
        # installs still see *something*. Ignore cache errors — they
        # must never break the hook.
        cc_pid = _claude_code_pid() or os.getppid()
        try:
            atomic_write_text(_cache_path_for_pid(cc_pid), text)
        except OSError:
            pass
        try:
            atomic_write_text(_legacy_cache_path(), text)
        except OSError:
            pass
        try:
            _gc_sessions_cache()
        except OSError:
            pass
        # Cache stores the full text so /lore:loaded can show everything;
        # additionalContext gets truncated only for the agent-facing inject.
        context_text = text
        if len(context_text) > MAX_CONTEXT_CHARS:
            context_text = (
                context_text[: MAX_CONTEXT_CHARS - 40]
                + "\n... (truncated — /lore:loaded for full)"
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


import typer  # noqa: E402

from lore_adapters import get_adapter  # noqa: E402
from lore_core.hook_log import HookEventLogger  # noqa: E402
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry, WikiLedger  # noqa: E402
from lore_core.scope_resolver import resolve_scope  # noqa: E402
from lore_cli._compat import argv_main  # noqa: E402

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


@hook_app.command("session-start")
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
    cwd_resolved = Path(_resolve_cwd(cwd))
    out = _session_start(str(cwd_resolved))

    # Surface pending `.lore.yml` offers at the top of the banner.
    try:
        notice = _offer_notice_line(cwd_resolved)
        if notice:
            out = notice + "\n\n" + out
    except Exception:
        pass

    # Resolve scope once — reused by the banner, curator spawns, and
    # transcript sync below. None means "unattached cwd."
    scope = resolve_scope(cwd_resolved)
    lore_root = _infer_lore_root(scope.claude_md_path) if scope is not None else None

    # Attempt to append capture-state breadcrumb banner
    try:
        from datetime import UTC, datetime as dt
        from lore_cli.breadcrumb import BannerContext, render_banner
        from lore_core.config import get_wiki_root

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
                except Exception:
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
                except Exception:
                    pass

                try:
                    cross = _cross_scope_breadcrumbs(lore_root, scope.wiki)
                    if cross:
                        out = out + "\n" + "\n".join(cross)
                except Exception:
                    pass
    except Exception:
        # Banner generation failure is non-fatal — proceed without it.
        pass

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
    out = _pre_compact(_resolve_cwd(cwd))
    _emit("PreCompact", out, plain=plain)


@hook_app.command("stop")
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
    out = _stop()
    _emit("Stop", out, plain=plain)


@hook_app.command("live-state")
def cmd_live_state() -> None:
    """Print live capture state + the SessionStart cache."""
    sys.stdout.write(_live_state())


# ---------------------------------------------------------------------------
# UserPromptSubmit heartbeat
# ---------------------------------------------------------------------------


def _heartbeat(
    lore_root: Path,
    cwd: Path,
    wiki_cfg: "WikiConfig",
    *,
    pid: int | None = None,
) -> tuple[str | None, str | None]:
    """Check drain for new events; return (system_message, additional_context).

    Both may be None. Cooldown-gated: returns (None, None) immediately
    when the stamp is fresh.
    """
    from lore_core.drain import SYSTEM_SESSION, DrainStore

    hb = wiki_cfg.heartbeat
    if not hb.enabled:
        return None, None

    stamp = lore_root / ".lore" / "curator-heartbeat.spawn.stamp"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    if _stamp_within_cooldown(stamp, hb.cooldown_s):
        return None, None

    effective_pid = pid or _claude_code_pid() or os.getpid()
    cursor_path = lore_root / ".lore" / "drain" / f"heartbeat-{effective_pid}.cursor"

    # Read cursor (shared concept — SessionStart advances the session
    # drain cursor; heartbeat uses its own PID-scoped cursor over the
    # system drain so the two don't interfere).
    cursor_ts = None
    if cursor_path.exists():
        try:
            from datetime import datetime as _dt, UTC as _UTC
            raw = cursor_path.read_text().strip()
            if raw:
                cursor_ts = _dt.fromisoformat(raw).replace(tzinfo=_UTC)
        except (OSError, ValueError):
            pass

    system_store = DrainStore(lore_root, SYSTEM_SESSION)
    events = system_store.read(since=cursor_ts, limit=200)

    if not events:
        _write_stamp(stamp)
        return None, None

    counts = _tally_drain(events)
    summary = _format_drain_summary(counts, events)
    sys_msg = f"lore: {summary}" if summary else None

    # Build additionalContext with wikilinks when push_context is on.
    ctx = None
    if hb.push_context and events:
        wikilinks = []
        for e in events:
            wl = e.data.get("wikilink")
            if wl:
                wikilinks.append(wl)
        if wikilinks:
            ctx = "New in vault: " + ", ".join(dict.fromkeys(wikilinks))

    # Advance cursor to newest + 1µs.
    from datetime import timedelta
    newest = max(e.ts for e in events)
    try:
        tmp = cursor_path.with_suffix(".cursor.tmp")
        tmp.write_text((newest + timedelta(microseconds=1)).isoformat())
        os.replace(tmp, cursor_path)
    except OSError:
        pass

    _write_stamp(stamp)
    return sys_msg, ctx


@hook_app.command("user-prompt-submit")
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
    cwd_resolved = Path(_resolve_cwd(cwd))
    scope = resolve_scope(cwd_resolved)
    if scope is None:
        return
    lore_root = _infer_lore_root(scope.claude_md_path)
    wiki_cfg = _load_wiki_cfg_from_scope(scope, lore_root)

    sys_msg, ctx = _heartbeat(lore_root, cwd_resolved, wiki_cfg)
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


# ---------------------------------------------------------------------------
# Capture hook helpers
# ---------------------------------------------------------------------------


def _resolve_cwd_capture() -> Path:
    """Resolve CWD for capture: $CLAUDE_PROJECT_DIR → os.getcwd()."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(os.getcwd())


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
        try:
            subprocess.Popen(
                cmd,
                cwd=str(lore_root),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return False
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
    import socket
    import subprocess
    # Cheap test override.
    env_email = os.environ.get("GIT_AUTHOR_EMAIL")
    if env_email:
        return env_email
    try:
        res = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return socket.gethostname()
    except OSError:
        return ""


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
    n_synced = counts.get("transcript-synced", 0)
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
    if n_synced:
        parts.append(f"{n_synced} transcript{'s' if n_synced != 1 else ''} synced")
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
    host: str = typer.Option("claude-code", help="Adapter host name."),
) -> None:
    """Hot-path capture hook — called by Claude Code on SessionEnd / PreCompact / SessionStart.

    Must return in <100ms. Updates the sidecar ledger; spawns detached
    curator when pending work exceeds threshold. No LLM, no network,
    bounded FS walk (8 levels).
    """
    if _in_curator_mode():
        return
    import time as _time
    from lore_adapters import UnknownHostError
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
        from lore_core.config import get_lore_root as _glr
        _vault = _glr().resolve()
        if Path(cwd).resolve() == _vault and resolve_scope(cwd) is None:
            return
    except Exception:
        pass

    scope = resolve_scope(cwd)
    if scope is None:
        # Unattached cwd — no ledger work to do, but we still emit a hook
        # event so "hook fired but declined" is distinguishable from "hook
        # never fired" in `lore status` / `lore runs list --hooks`.
        from lore_core.config import get_lore_root
        try:
            HookEventLogger(get_lore_root()).emit(
                event=event, host=host, scope=None,
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
            adapter = get_adapter(host)
        except UnknownHostError:
            logger.emit(
                event=event, host=host, scope=scope_payload,
                duration_ms=int((_time.monotonic() - start) * 1000),
                outcome="error",
                pending_after=0,
                error={"type": "UnknownHostError", "message": host},
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
        to_write: list[TranscriptLedgerEntry] = []
        for h in handles:
            entry = tledger.get(h.host, h.id)
            if entry is None:
                to_write.append(
                    TranscriptLedgerEntry(
                        host=h.host,
                        transcript_id=h.id,
                        path=h.path,
                        directory=h.cwd,
                        digested_hash=None,
                        digested_index_hint=None,
                        synthesised_hash=None,
                        last_mtime=h.mtime,
                        curator_a_run=None,
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
            event=event, host=host, scope=scope_payload,
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
            event=event, host=host, scope=scope_payload,
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


main = argv_main(hook_app)


if __name__ == "__main__":
    sys.exit(main())
