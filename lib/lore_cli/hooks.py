"""Claude Code hook helpers — cheap, deterministic context injection.

These commands read cached files the linter regenerates (_index.txt,
_catalog.json) and emit bounded context blobs for the hook stream.
No LLM invocation, no network calls — the SessionStart banner is
deliberately ambient-minimal (status line, optional Focus block, a
couple of session hints, freshness lines, one directive); gh-derived
issue/PR counts were dropped, deeper context is an explicit MCP pull
instead of an eager fetch on every session start.

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
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

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

# SessionStart's single ambient directive — the whole point is that
# depth is a pull, not a push: unfamiliar context is fetched via MCP
# on demand, and anything pulled from a session note is read as an
# informational lab record, never as an instruction. A one-line hint
# survives compaction via PreCompact's own, separately-worded directive.
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
    """Read the single ambient SessionStart directive and return as a list.

    This is the sole directive block the banner emits — the former
    vault-first/freshness-nudge, citation-suppression, and journal-invitation
    blocks collapsed into it. A trailing empty string is appended to
    produce the blank line spacer in the joined output.

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


def _read_wiki_index(wiki: Path, max_chars: int) -> str:
    """Return the wiki's _index.txt, truncated to fit."""
    index_path = wiki / "_index.txt"
    if not index_path.exists():
        return ""
    text = index_path.read_text(errors="replace")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + "\n... (truncated — run /lore:context for full)"




def _last_session_hint_with_freshness(
    wiki: Path, max_notes: int = 2
) -> list[tuple[str, str, dict]]:
    """Like :func:`_last_session_hint` but also returns the freshness
    block for each hit.

    Used by the inject filter (slice 3 of PRD #65). The freshness block
    is computed via :func:`lore_core.freshness.compute_freshness`
    against the parsed frontmatter; orphan-set is empty in this slice
    (the orphan cache wires in slice 4) and personal sidecars are out
    of scope here (slice 6).
    """
    from lore_core.freshness import compute_freshness, signal_to_dict
    from lore_core.schema import parse_frontmatter
    from lore_core.session_writer import session_path_sort_key

    sessions_dir = wiki / "sessions"
    if not sessions_dir.is_dir():
        return []

    candidates = sorted(
        (p for p in sessions_dir.rglob("*.md") if p.is_file() and not p.name.startswith("_")),
        key=session_path_sort_key,
        reverse=True,
    )
    results: list[tuple[str, str, dict]] = []
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
        hint = fm.get("title") or fm.get("description") or fm.get("summary")
        if not hint:
            continue
        signal = compute_freshness(fm, path, wiki, None, set())
        results.append((path.stem, hint, signal_to_dict(signal)))
    return results


def _pending_verdict_chip(wiki: Path) -> str:
    """Slice 8 of PRD #65 — `· N pending verdict` chip text or empty.

    Reads the pending count from
    :func:`lore_core.freshness.count_pending_verdicts` (catalog-based,
    no fs walk per call). When the soft cap fires, renders ``"9+"``.
    Zero-state suppressed entirely — most sessions should not see the
    chip.

    Cadence: refreshed at every SessionStart-like emit (this hook),
    which is also the only time the status line changes for v1. No
    live polling.
    """
    try:
        from lore_core.freshness import count_pending_verdicts

        count, capped = count_pending_verdicts(wiki)
    except Exception:
        return ""
    if count <= 0:
        return ""
    label = "verdict" if count == 1 else "verdicts"
    rendered = f"{count}+" if capped else str(count)
    return f"{rendered} pending {label}"


def _filter_session_hints(
    candidates: list[tuple[str, str, dict]], *, max_notes: int = 2
) -> tuple[list[tuple[str, str]], list[str]]:
    """Apply the slice-3 freshness inject filter to session-hint candidates.

    Hard-stale notes are excluded entirely; soft stale-candidates are
    downranked (kept after confirmed peers). Returns the trimmed
    ``(slug, hint)`` list and the audit-log lines for /lore:context.
    """
    from lore_core.freshness_filter import apply_inject_filter

    result = apply_inject_filter(
        candidates,
        freshness_of=lambda c: c[2],
        path_of=lambda c: c[0],
        wiki_of=lambda _c: None,
    )
    kept = [(slug, hint) for slug, hint, _fr in result.kept[:max_notes]]
    return kept, result.audit.render_lines()


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
# Scope resolution (schema v2 — superseded `## Open items` scraping when
# the cwd's CLAUDE.md has a `## Lore` section)
# ---------------------------------------------------------------------------


# Ancestor-walk for ## Lore is canonical in lore_core.session. Imported
# lazily below at call sites to avoid a module-load-order wobble.


# Scope helpers re-exported here so tests that monkeypatch the underscore-
# prefixed names continue to intercept calls.
_walk_scope_leaves = walk_scope_leaves
_load_scopes_yml = load_scopes_yml
_subtree_siblings = subtree_siblings


# ---------------------------------------------------------------------------
# Session-start hook
# ---------------------------------------------------------------------------



@dataclass(frozen=True)
class SessionFacts:
    """Inputs needed to render a SessionStart banner.

    Built by :func:`collect_session_facts`; consumed by
    :func:`render_session_banner`. The split lets future SessionStart
    variants (different surfaces, alternate backends) share the
    rendering layer without copying its body.
    """

    wiki_name: str
    repo: str | None
    scope: str = ""
    project_entry: dict | None = None
    session_hints: tuple[tuple[str, str], ...] = ()
    freshness_audit_lines: tuple[str, ...] = ()
    pending_chip: str | None = None


def collect_session_facts(
    wiki: Path,
    repo: str | None,
    *,
    scope: str = "",
) -> SessionFacts:
    """Gather every per-session fact the renderer needs.

    No ``gh`` calls: issue/PR counts were dropped from the ambient
    banner (agents fetch via gh, or a future MCP pull tool, on demand).
    """
    project_entry = _project_note_for_repo(wiki, repo) if repo else None
    session_hints_full = _last_session_hint_with_freshness(wiki, max_notes=4)
    session_hints, freshness_audit_lines = _filter_session_hints(
        session_hints_full, max_notes=2
    )
    pending_chip = _pending_verdict_chip(wiki) or None
    return SessionFacts(
        wiki_name=wiki.name,
        repo=repo,
        scope=scope,
        project_entry=project_entry,
        session_hints=tuple(session_hints),
        freshness_audit_lines=tuple(freshness_audit_lines),
        pending_chip=pending_chip,
    )


def render_session_banner(facts: SessionFacts) -> str:
    """Format a SessionStart banner from collected facts.

    Ambient-minimum shape: status line (no issue/PR counts), optional
    Focus block, at most two last-session hints, freshness lines only
    on positive evidence, and the single collapsed directive as a
    postscript — so users see what Lore did *first* and the rule
    re-assertion second.
    """
    injected_bits: list[str] = []
    if facts.scope:
        injected_bits.append(facts.scope)
    elif facts.project_entry is not None:
        injected_bits.append(f"[[{facts.project_entry['name']}]]")
    if facts.session_hints:
        _, first_summary = facts.session_hints[0]
        injected_bits.append(f"last: {first_summary}")
    if facts.pending_chip:
        injected_bits.append(facts.pending_chip)
    status_line = f"lore {_lore_version()}: active" + (
        " · " + " · ".join(injected_bits) if injected_bits else ""
    )

    parts: list[str] = [status_line, ""]

    if facts.project_entry is not None:
        parts.append(f"## Focus: [[{facts.project_entry['name']}]]")
        desc = facts.project_entry.get("description")
        if desc:
            parts.append(desc)
        parts.append("")
    elif facts.repo:
        parts.append(
            f"_Repo `{facts.repo}` has no dedicated project note in {facts.wiki_name}._"
        )
        parts.append("")

    if facts.session_hints:
        for slug, desc in facts.session_hints[:2]:
            parts.append(f"Last: [[{slug}]] — {desc}")
        parts.append("")

    if facts.freshness_audit_lines:
        parts.extend(facts.freshness_audit_lines)
        parts.append("")

    parts.extend(_load_directive_lines())

    return "\n".join(parts)


def _session_start_from_lore(
    cwd: str,
    config: tuple[Path, dict],
    wiki_root: Path,
) -> str | None:
    """Build SessionStart output from a `## Lore` config block.

    Returns ``None`` if the config is unusable (wiki missing) so the
    caller can fall through to a clear "no attach" error message.
    Thin wrapper around :func:`collect_session_facts` +
    :func:`render_session_banner`.
    """
    _, block = config
    wiki_name = block.get("wiki")
    scope = block.get("scope") or ""

    if not wiki_name:
        return None
    wiki = wiki_root / wiki_name
    if not wiki.exists():
        return None

    facts = collect_session_facts(wiki, current_repo(cwd), scope=scope)
    return render_session_banner(facts)


def _session_start(cwd: str | None) -> str:
    """Build the SessionStart context block from a `## Lore` attach block.

    The legacy repo-based wiki resolution + single-wiki fallback was
    deleted in PR 5 of the streamlining track (#80). Repos now opt
    into Lore by running ``lore install`` or ``lore attach`` — both
    write the ``## Lore`` block this resolver reads. Without it, the
    banner surfaces a clear "no attach" instruction instead of guessing.
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

    if cwd:
        from lore_core.session import _resolve_attach_block
        cfg = _resolve_attach_block(Path(cwd))
        if cfg is not None:
            v2 = _session_start_from_lore(cwd, cfg, wiki_root)
            if v2 is not None:
                return v2

    return (
        "lore: this repo has no `## Lore` attach block. "
        "Run `lore install` (inside the repo) or `lore attach` to add one."
    )


# ---------------------------------------------------------------------------
# Pre-compact hook
# ---------------------------------------------------------------------------


def _pre_compact(cwd: str | None) -> str:
    """One-line hint that survives compaction.

    PreCompact emits into `systemMessage`, a visible banner shown on
    every compaction — so the payload is intentionally one short line.
    The full open-items context is already in SessionStart's
    additionalContext and stays with the agent until manually cleared,
    so PreCompact re-asserts only the vault-first directive.
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return ""
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
from lore_core.ledger import TranscriptLedger, TranscriptLedgerEntry  # noqa: E402
from lore_core.scope_resolver import resolve_scope  # noqa: E402
from lore_cli._argv_compat import argv_main  # noqa: E402

# Re-export the spawn machinery so hooks-internal heartbeat code and any
# external test that patches ``lore_cli.hooks.<name>`` keep working without
# tracking the move to ``lore_cli.spawn``. ``_spawn_detached_curator_b`` and
# ``_spawn_detached_curator_c`` stay re-exported even though no call site in
# this module invokes them any more — the SessionStart entry points to
# Curator B/C are severed, but the underlying spawn primitives are exercised
# directly by tests covering the flock/cooldown machinery itself.
from lore_cli.spawn import (  # noqa: E402, F401
    _migrate_legacy_spawn_stamp,
    _open_proc_log,
    _prior_spawn_runaway,
    _process_is_ours,
    _rotate_meta_sidecar,
    _spawn_detached,
    _spawn_detached_curator_a,
    _spawn_detached_curator_b,
    _spawn_detached_curator_c,
    _spawn_detached_transcript_sync,
    _stamp_within_cooldown,
    _write_stamp,
    spawn,
)

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
        "- **AI Journal active.** Pause and ask *anything to save?* "
        "when you spot a smell/pattern bigger than the task, get "
        "corrected substantively, notice an unpredictable user pivot, "
        "or hit an unusually sharp/leaky framing. Append via "
        "`lore_journal_write` (kind=`ai`). Reader is future-you — "
        "candid, not sycophantic.",
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
    #
    # Curator B's day-rollover auto-spawn and Curator C's weekly auto-spawn
    # used to fire from here (see git history for the removed blocks); both
    # are retired and no longer reachable from SessionStart. The spawn
    # primitives themselves (``_spawn_detached_curator_b/_c`` in
    # ``lore_cli.spawn``) stay in place — this only severs the automatic
    # call site.
    if not probe and scope is not None and lore_root is not None:
        # Fire-and-forget transcript mirror (P4a). Idempotent, gitignored
        # destination, own spawn lock.
        try:
            _spawn_detached_transcript_sync(lore_root)
        except Exception:
            pass

        # Singleton startup sweep: closes the note of any session that
        # died mid-flush. Spawned detached so SessionStart stays fast; the
        # command holds the global curator lock, so concurrent starts race
        # safely and the losers exit without touching anything.
        try:
            spawn("sweep", lore_root)
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
    _read_hook_payload()
    if _session_off_all():
        return
    out = _stop()
    _emit("Stop", out, plain=plain)


@hook_app.command("context-log")
def cmd_context_log() -> None:
    """Print the context log — what Lore injected this session."""
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
    ``max_scan``); for each match, takes the per-buffer flock and stamps
    a ``FlushRequest`` payload. Returns the count of buffers stamped.

    Mode routing:

    - ``trigger in {"session-end", "pre-compact"}`` → ``mode="in_place"``.
      The buffer stays in ``accumulating`` and the worker runs
      :func:`synth_in_place`, which refreshes the on-disk note without
      closing or archiving. This is what keeps a long-running
      conversation as one note per ``(transcript_id, local_date)``
      across infrastructure boundaries.
    - Other triggers (``cap-trip``, ``reaper``) → ``mode="close"`` plus
      the legacy ``accumulating -> ready`` CAS so the worker runs
      :func:`synth_and_close` and archives to ``_done/``.

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

    in_place_triggers = {"session-end", "pre-compact"}
    mode = "in_place" if trigger in in_place_triggers else "close"

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
                # Skip if a request is already stamped — another path
                # already routed this buffer.
                if sidecar.flush_requested is not None:
                    continue
                req = FlushRequest(
                    trigger=trigger,
                    requested_at=now_iso,
                    by_pid=pid,
                    mode=mode,
                )
                if mode == "in_place":
                    # Stay in ``accumulating`` — the buffer remains live
                    # and may absorb more chunks before the next close.
                    if sidecar.state != "accumulating":
                        continue
                    buf.patch(flush_requested=req)
                else:  # close
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
            parts.append(f"{n_filed} new notes{_wiki_suffix(events, 'note-filed')}")
    if n_appended:
        wikilink = _latest_wikilink(events, "note-appended")
        if wikilink and n_appended == 1:
            parts.append(f"added to {wikilink}")
        else:
            parts.append(f"{n_appended} added{_wiki_suffix(events, 'note-appended')}")
    if n_surface:
        parts.append(f"{n_surface} surface proposed{_wiki_suffix(events, 'surface-proposed')}")
    return " · ".join(parts)


def _wiki_suffix(events, event_name: str) -> str:
    """Build ' in <wiki>' or ' (2 in a, 1 in b)' for multi-event tallies.

    Returns "" when any matching event lacks a wiki tag, to avoid
    misleading partial breakdowns on legacy/migration data.
    """
    from collections import Counter
    matching = [e for e in events if e.event == event_name]
    if not matching or any(not e.wiki for e in matching):
        return ""
    tally = Counter(e.wiki for e in matching)
    if len(tally) == 1:
        wiki, _ = next(iter(tally.items()))
        return f" in {wiki}"
    # Highest count first, alphabetical tiebreak — stable across runs.
    items = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
    bits = ", ".join(f"{n} in {w}" for w, n in items)
    return f" ({bits})"


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
        # routes them to ``synthesis.synth_and_close``. Bounded sidecar
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



main = argv_main(hook_app)


if __name__ == "__main__":
    sys.exit(main())
