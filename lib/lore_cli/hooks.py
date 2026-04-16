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

# Keep auto-injected context bounded. ~500 tokens ≈ ~2000 characters for
# prose; we cap at 3000 to allow some structure.
MAX_CONTEXT_CHARS = 3000
RECENT_SESSION_DAYS = 7


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
    except (OSError, Exception):
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


def _recent_open_items(wiki: Path, days: int = RECENT_SESSION_DAYS) -> list[str]:
    """Parse `## Open items` sections from recent session notes. Deduped."""
    sessions_dir = wiki / "sessions"
    if not sessions_dir.is_dir():
        return []
    cutoff = date.today() - timedelta(days=days)
    items: list[str] = []
    seen: set[str] = set()
    for md in sorted(sessions_dir.glob("*.md"), reverse=True):
        try:
            iso = md.stem[:10]
            d = date.fromisoformat(iso)
        except (ValueError, IndexError):
            continue
        if d < cutoff:
            continue
        text = md.read_text(errors="replace")
        m = _OPEN_ITEMS_RE.search(text)
        if not m:
            continue
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("-") is False:
                continue
            body = line.lstrip("-").strip()
            if not body or body.lower() == "none":
                continue
            if body in seen:
                continue
            seen.add(body)
            items.append(body)
    return items


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
    """Build the SessionStart context block."""
    import os

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        # Visible diagnostic — silent failure is worse than an "I'm here" line
        hint = os.environ.get("LORE_ROOT") or "(unset, defaulting to ~/lore)"
        return (
            f"lore: no vault at LORE_ROOT={hint}. "
            "Set LORE_ROOT to your vault path or run `lore init` to scaffold one."
        )

    # Repo-scoped wiki resolution
    repo = current_repo(cwd)
    wiki = _wiki_for_repo(repo) if repo else None

    # Fall back to the only wiki if there's exactly one
    if wiki is None:
        wikis = [p for p in sorted(wiki_root.iterdir()) if p.resolve().is_dir()]
        if len(wikis) == 1:
            wiki = wikis[0]

    if wiki is None:
        if repo:
            return (
                f"lore: no wiki covers `{repo}` in {wiki_root}. "
                "Tag a wiki's `.lore-hints.yml` with this repo or "
                "run `/lore:session` — session auto-tags populate over time."
            )
        return f"lore: no wiki resolved in {wiki_root}. Pick one with `/lore:resume <wiki>`."

    index_text = _read_wiki_index(wiki, MAX_CONTEXT_CHARS - 400)
    open_items = _recent_open_items(wiki)
    stale = _stale_count(wiki)

    catalog = _wiki_catalog(wiki) or {}
    stats = catalog.get("stats", {})
    note_count = stats.get("total_notes", "?")

    parts: list[str] = []
    # One-liner status — users see this as the first line
    stale_tag = f", {stale} stale flagged" if stale else ""
    status_line = (
        f"lore: loaded {wiki.name} ({note_count} notes, "
        f"{len(open_items)} open items{stale_tag}) · /lore:why"
    )
    parts.append(status_line)

    if repo:
        parts.append(f"\n_Scoped to repo `{repo}`._\n")

    if open_items:
        parts.append("## Open items (recent sessions)")
        parts.append("")
        # Cap to 8 items
        for item in open_items[:8]:
            parts.append(f"- {item}")
        if len(open_items) > 8:
            parts.append(f"- … ({len(open_items) - 8} more; /lore:resume to expand)")
        parts.append("")

    if index_text:
        parts.append("## Knowledge index")
        parts.append("")
        parts.append(index_text)

    out = "\n".join(parts)
    if len(out) > MAX_CONTEXT_CHARS:
        out = out[: MAX_CONTEXT_CHARS - 40] + "\n... (truncated — /lore:why for full)"
    return out


# ---------------------------------------------------------------------------
# Pre-compact hook
# ---------------------------------------------------------------------------


def _pre_compact(cwd: str | None) -> str:
    """Build a minimal open-items summary to survive compaction.

    Kept deliberately thinner than session-start: only the items, no index
    (the agent has already seen the index this session).
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

    open_items = _recent_open_items(wiki)
    if not open_items:
        return ""

    parts = [
        "## Open items (carry across compaction)",
        "",
    ]
    for item in open_items[:10]:
        parts.append(f"- {item}")
    return "\n".join(parts) + "\n"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-hook")
    sub = parser.add_subparsers(dest="hook", required=True)

    ss = sub.add_parser("session-start", help="Inject vault context at session start")
    ss.add_argument("--cwd", help="Project working directory (Claude Code provides this)")

    pc = sub.add_parser("pre-compact", help="Inject open items before compaction")
    pc.add_argument("--cwd", help="Project working directory")

    sub.add_parser("stop", help="Prompt to write a session note")

    args = parser.parse_args(argv)

    # Claude Code passes CLAUDE_PROJECT_DIR in env; honor it as fallback
    cwd = getattr(args, "cwd", None) or os.environ.get("CLAUDE_PROJECT_DIR")

    if args.hook == "session-start":
        out = _session_start(cwd)
    elif args.hook == "pre-compact":
        out = _pre_compact(cwd)
    elif args.hook == "stop":
        out = _stop()
    else:
        return 2

    if out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
