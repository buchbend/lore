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

from lore_cli.attach_cmd import read_attach


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
        if cmdline == "claude" or cmdline.rstrip() == "claude":
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
    if repo in text or (tail and tail in text):
        return True
    return False


def _is_ephemeral(item: str) -> bool:
    lower = item.lower()
    return any(marker in lower for marker in EPHEMERAL_MARKERS)


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


MAX_ANCESTOR_WALK = 20
GH_TIMEOUT_SECONDS = 10
MAX_ISSUES_INLINE = 5
MAX_PRS_INLINE = 3


def _find_lore_config(cwd: Path) -> tuple[Path, dict] | None:
    """Walk up from cwd looking for an ancestor CLAUDE.md with `## Lore`.

    Returns (claude_md_path, parsed_block) or None if not found within
    MAX_ANCESTOR_WALK steps. The walk terminates at the filesystem root.
    """
    current = cwd.resolve()
    for _ in range(MAX_ANCESTOR_WALK):
        claude_md = current / "CLAUDE.md"
        if claude_md.exists():
            block = read_attach(claude_md)
            if block:
                return claude_md, block
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


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
        cfg = _find_lore_config(Path(cwd))
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


def _why() -> str:
    """Return the SessionStart cache for the current Claude Code session.

    Resolution order:
      1. `$LORE_CACHE/sessions/<claude_code_pid>.md` (per-session, crosstalk-free)
      2. `$LORE_CACHE/last-session-start.md` (legacy fallback, may belong
         to a different concurrent session — flagged as such)
      3. An explanatory error string if nothing is cached yet.
    """
    cc_pid = _claude_code_pid()
    if cc_pid is not None:
        primary = _cache_path_for_pid(cc_pid)
        if primary.exists():
            try:
                return primary.read_text(errors="replace")
            except OSError:
                pass

    legacy = _legacy_cache_path()
    if legacy.exists():
        try:
            body = legacy.read_text(errors="replace")
        except OSError:
            body = ""
        if body:
            note = (
                "_(read from legacy singleton cache — may be from a "
                "different concurrent Claude session)_\n\n"
            )
            return note + body

    return (
        "lore: no SessionStart cache found. Either the hook has not "
        "fired yet in this session, or hooks are disabled. Check "
        "`~/.claude/settings.json` for a SessionStart entry invoking "
        "`lore hook session-start`, or re-run the installer with "
        "`--with-hooks`.\n"
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
    elif hook_event == "PreCompact":
        envelope = {"systemMessage": text}
    elif hook_event == "Stop":
        envelope = {"systemMessage": text.strip()}
    else:
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


@hook_app.command("session-start")
def cmd_session_start(
    cwd: str = typer.Option(None, "--cwd", help="Project working directory."),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Print raw text instead of Claude Code JSON envelope.",
    ),
) -> None:
    """Inject vault context at session start."""
    out = _session_start(_resolve_cwd(cwd))

    # Attempt to append capture-state breadcrumb banner
    try:
        from datetime import UTC, datetime as dt
        from lore_cli.breadcrumb import BannerContext, render_banner
        from lore_core.config import get_wiki_root

        cwd_resolved = Path(_resolve_cwd(cwd))
        scope = resolve_scope(cwd_resolved)
        if scope is not None:
            wiki_root = get_wiki_root()
            if wiki_root.exists():
                lore_root = _infer_lore_root(scope.claude_md_path)
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
    except Exception:
        # Banner generation failure is non-fatal — proceed without it.
        pass

    # Auto-trigger Curator B on calendar-day rollover
    try:
        from datetime import UTC, datetime as dt

        cwd_resolved = Path(_resolve_cwd(cwd))
        scope = resolve_scope(cwd_resolved)
        if scope is not None:
            lore_root = _infer_lore_root(scope.claude_md_path)
            wledger = WikiLedger(lore_root, scope.wiki)
            wentry = wledger.read()
            today = dt.now(UTC).date()
            last_b_date = wentry.last_curator_b.date() if wentry.last_curator_b else None
            if last_b_date is None or today > last_b_date:
                _spawn_detached_curator_b(lore_root, scope.wiki)
    except Exception:
        # Spawn failure is non-fatal — proceed without it.
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
    out = _stop()
    _emit("Stop", out, plain=plain)


@hook_app.command("why")
def cmd_why() -> None:
    """Print the SessionStart cache for the current Claude session."""
    sys.stdout.write(_why())


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


def _spawn_detached_curator_a(lore_root: Path) -> None:
    """Fire-and-forget subprocess that runs `lore curator run` detached.

    On POSIX uses start_new_session=True; on Windows uses
    CREATE_NEW_PROCESS_GROUP (stub for v1; we're Unix-first).
    """
    import subprocess
    cmd = [sys.executable, "-m", "lore_cli", "curator", "run"]
    env = os.environ.copy()
    env["LORE_ROOT"] = str(lore_root)
    try:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        # Fire-and-forget — don't propagate failures.
        pass


def _spawn_detached_curator_b(lore_root: Path, wiki_name: str) -> None:
    """Fire-and-forget subprocess that runs Curator B detached.

    Invokes: lore curator run --abstract --wiki <wiki_name>

    On POSIX uses start_new_session=True for detached execution.
    """
    import subprocess
    cmd = [sys.executable, "-m", "lore_cli", "curator", "run", "--abstract", "--wiki", wiki_name]
    env = os.environ.copy()
    env["LORE_ROOT"] = str(lore_root)
    try:
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        # Fire-and-forget — don't propagate failures.
        pass


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
    import time as _time
    from lore_adapters import UnknownHostError

    start = _time.monotonic()
    cwd = cwd_override or _resolve_cwd_capture()
    scope = resolve_scope(cwd)
    if scope is None:
        # Unattached cwd — silently no-op. Not an error.
        return

    lore_root = _infer_lore_root(scope.claude_md_path)
    logger = HookEventLogger(lore_root)
    outcome = "no-new-turns"
    run_id: str | None = None
    pending_after = 0
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
            )
            raise typer.Exit(code=1)

        if transcript is not None:
            handles = [h for h in adapter.list_transcripts(cwd) if h.path == transcript]
        else:
            handles = adapter.list_transcripts(cwd)

        for h in handles:
            entry = tledger.get(h.host, h.id)
            if entry is None:
                entry = TranscriptLedgerEntry(
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
                tledger.upsert(entry)
            elif entry.last_mtime != h.mtime:
                entry.last_mtime = h.mtime
                tledger.upsert(entry)

        pending = tledger.pending()
        pending_after = len(pending)
        cfg = _load_wiki_cfg_from_scope(scope, lore_root)
        if pending_after >= cfg.curator.threshold_pending:
            _spawn_detached_curator_a(lore_root)
            outcome = "spawned-curator"
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
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise
    else:
        logger.emit(
            event=event, host=host, scope=scope_payload,
            duration_ms=int((_time.monotonic() - start) * 1000),
            outcome=outcome,
            pending_after=pending_after,
            run_id=run_id,
        )


main = argv_main(hook_app)


if __name__ == "__main__":
    sys.exit(main())
