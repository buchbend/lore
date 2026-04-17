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

import argparse
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from lore_core.config import get_wiki_root
from lore_core.git import current_repo
from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter

# Cache file SessionStart writes; /lore:why reads this instead of
# re-running the hook (no Bash subprocess, no sandbox, no permission
# prompt).
def _cache_path() -> Path:
    cache_dir = Path(
        os.environ.get("LORE_CACHE", str(Path.home() / ".cache" / "lore"))
    )
    return cache_dir / "last-session-start.md"

# Keep auto-injected context bounded. ~500 tokens ≈ ~2000 characters for
# prose; we cap at 2000 to stay tight.
MAX_CONTEXT_CHARS = 2000
RECENT_SESSION_DAYS = 14
MAX_OPEN_ITEMS_INLINE = 5

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
    return text[: max_chars - 40] + "\n... (truncated — run /lore:why for full)"


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
# Session-start hook
# ---------------------------------------------------------------------------


def _session_start(cwd: str | None) -> str:
    """Build the SessionStart context block.

    Scoping strategy:
      - Resolve the current git repo (if any) and the wiki that covers it
      - When a repo is resolved, center context on the matching project
        note (if found) and on open items from sessions that touched
        this repo. Other open items become a one-line "elsewhere" note.
      - When no repo is resolved, degrade to a wiki-level summary.
    """
    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        hint = os.environ.get("LORE_ROOT") or "(unset, defaulting to ~/lore)"
        return (
            f"lore: no vault at LORE_ROOT={hint}. "
            "Set LORE_ROOT to your vault path or run `lore init`."
        )

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
        f"{len(items)} open{stale_tag}) · /lore:why"
    )

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

    parts.append(
        f"Vault: {wiki.name} — use `lore_search` MCP tool or `/lore:resume <topic>` "
        "to pull deeper context on demand."
    )

    out = "\n".join(parts)
    if len(out) > MAX_CONTEXT_CHARS:
        out = out[: MAX_CONTEXT_CHARS - 40] + "\n... (truncated — /lore:why for full)"
    return out


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
    if not items:
        return ""

    scope = wiki.name if repo is None else f"{wiki.name}:{repo.rsplit('/', 1)[-1]}"
    return (
        f"lore: {len(items)} open items for {scope} carry past compaction — "
        "run /lore:resume if the agent needs them refreshed."
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

    `--plain` dumps raw text to stdout — used by the /lore:why skill and
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
        # Cache the full injected body so `/lore:why` can Read it
        # directly without invoking Bash (which triggers sandbox /
        # permission prompts on some setups). Ignore cache write
        # errors — they must never break the hook.
        try:
            atomic_write_text(_cache_path(), text)
        except OSError:
            pass
        envelope = {
            "systemMessage": one_liner,
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": text,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-hook")
    sub = parser.add_subparsers(dest="hook", required=True)

    for name, help_text in [
        ("session-start", "Inject vault context at session start"),
        ("pre-compact", "Inject open items before compaction"),
        ("stop", "Hint to capture a session note"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        if name != "stop":
            sp.add_argument("--cwd", help="Project working directory")
        sp.add_argument(
            "--plain",
            action="store_true",
            help="Print raw text instead of Claude Code JSON envelope",
        )

    args = parser.parse_args(argv)
    # Resolve CWD in order: explicit --cwd → CLAUDE_PROJECT_DIR env →
    # actual process working directory (Claude Code sets this to the
    # project dir when spawning hooks). Having a sensible default means
    # hook commands in settings.json don't need $CLAUDE_PROJECT_DIR
    # expansion — which avoids Claude Code's "simple_expansion"
    # permission gate.
    cwd = (
        getattr(args, "cwd", None)
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )

    if args.hook == "session-start":
        out = _session_start(cwd)
    elif args.hook == "pre-compact":
        out = _pre_compact(cwd)
    elif args.hook == "stop":
        out = _stop()
    else:
        return 2

    _emit(_HOOK_EVENT[args.hook], out, plain=args.plain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
