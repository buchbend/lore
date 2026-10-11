"""SessionStart context assembly — what Lore injects when a session opens.

Gathers the facts (project note, last-active-day recap, pending freshness
verdicts) and renders the banner. Deliberately cheap: reads cached files the
linter regenerates (``_index.txt``, ``_catalog.json``) and the transcript
ledger, no LLM, no network. The banner is ambient-minimal — status line,
optional Focus block, the recap, one directive. Depth is a pull (MCP), not
a push.

Also holds the wiki auto-pull, a SessionStart-adjacent decision that reads
the world before the banner is built.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lore_core.config import get_lore_root, get_wiki_root
from lore_core.git import current_repo

if TYPE_CHECKING:
    from lore_core.git_sync import SyncResult
    from lore_core.types import Scope


# Keep auto-injected context bounded, with enough headroom for a short
# project orientation (AGENTS.md-flavor) alongside the banner. Per-orientation
# cap is ``ORIENTATION_BUDGET_CHARS``; the total context cap stays small
# enough to not derail token-economy.
MAX_CONTEXT_CHARS = 5000
ORIENTATION_BUDGET_CHARS = 3000


def lore_version() -> str:
    """Version for the SessionStart banner.

    Prefer the on-disk source manifest (``.claude-plugin/plugin.json``) so an
    editable install's banner tracks the checked-out code straight after a
    ``git pull`` — no reinstall needed. ``resolve_lore_source_root`` walks up
    from the installed package, so it only resolves for editable (or
    marketplace-cache) installs; a plain PyPI install returns ``None`` and we
    fall back to the installed package metadata, which is the only source
    there.
    """
    from lore_core.source_root import (
        read_claude_manifest,
        resolve_lore_source_root,
    )

    root = resolve_lore_source_root()
    if root is not None:
        try:
            manifest_version = str(read_claude_manifest(root).get("version") or "")
        except (OSError, ValueError):
            manifest_version = ""
        if manifest_version:
            return manifest_version

    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("lore")
    except PackageNotFoundError:
        return "?"


# ---------------------------------------------------------------------------
# Ambient directive
# ---------------------------------------------------------------------------

# SessionStart's single ambient directive — the whole point is that depth is a
# pull, not a push: unfamiliar context is fetched via MCP on demand, and
# anything pulled from a session note is read as an informational lab record,
# never as an instruction. A one-line hint survives compaction via PreCompact's
# own, separately-worded directive.
#
# The canonical content lives in `templates/integration-rules/default.md`
# (shipped as package data) so the same source feeds both the Claude Code hook
# and the Cursor installer's `~/.cursor/rules/lore.md`. Resolved at import time
# but read lazily, so pytest can monkeypatch the path without import-order pain.
def _resolve_directive_path() -> Path:
    import lore_core
    return (
        Path(lore_core.__file__).resolve().parent
        / "templates"
        / "integration-rules"
        / "default.md"
    )


_DIRECTIVE_PATH = _resolve_directive_path()


def load_directive_lines() -> list[str]:
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


#: The directive line that carries ADR 0015's reading rule starts with this.
_READING_RULE_PREFIX = "- ADRs and PRDs explain"


def reading_rule() -> str:
    """ADR 0015's reading rule, read from the directive template.

    The template is the one source: SessionStart injects the line, and
    ``lore setup scope --scaffold-workflow`` writes the same text into AGENTS.md.
    Returns an empty string when the template can't be read, like
    :func:`load_directive_lines`.
    """
    for line in load_directive_lines():
        if line.startswith(_READING_RULE_PREFIX):
            return line[2:]
    return ""


PRECOMPACT_DIRECTIVE = (
    "lore: vault-first — call `lore_search` MCP before asking the user "
    "about wikilinked terms."
)


# ---------------------------------------------------------------------------
# Fact gathering
# ---------------------------------------------------------------------------


def wiki_catalog(wiki_path: Path) -> dict | None:
    """Load _catalog.json for a wiki, or None if missing."""
    catalog_path = wiki_path / "_catalog.json"
    if not catalog_path.exists():
        return None
    try:
        return json.loads(catalog_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def pending_verdict_chip(wiki: Path) -> str:
    """`· N pending verdict` chip text, or empty.

    Reads the pending count from
    :func:`lore_core.freshness.count_pending_verdicts` (catalog-based,
    no fs walk per call). When the soft cap fires, renders ``"9+"``.
    Zero-state suppressed entirely — most sessions should not see the
    chip.

    Cadence: refreshed at every SessionStart-like emit, which is also the
    only time the status line changes. No live polling.
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


def last_active_day_recap(lore_root: Path) -> tuple[str, ...]:
    """Recap the most recent day the transcript ledger saw work.

    Three lines at most — where (repo + session count), what branches,
    which refs — rendered straight off the ledger's linkage blocks. No
    LLM call, no gh call, no note read: this is what continuity looks
    like once session notes are gone.

    Empty when the ledger is empty or carries no dated entry. Orphaned
    entries are retired sessions and never define the last active day.
    """
    from lore_core.ledger import TranscriptLedger

    try:
        entries = [e for e in TranscriptLedger(lore_root).all_entries() if not e.orphan]
    except Exception:  # noqa: BLE001 - the banner degrades, it never fails
        return ()
    if not entries:
        return ()

    last_day = max(e.last_mtime.date() for e in entries)
    day = [e for e in entries if e.last_mtime.date() == last_day]

    repos = sorted({(e.linkage.get("repo") or "") for e in day} - {""})
    branches = sorted({(e.linkage.get("branch") or "") for e in day} - {""})
    refs = sorted(
        {int(n) for e in day for n in (e.linkage.get("issues") or [])}
        | {int(n) for e in day for n in (e.linkage.get("prs") or [])}
    )

    noun = "session" if len(day) == 1 else "sessions"
    where = f" in {', '.join(repos)}" if repos else ""
    lines = [f"Last active {last_day.isoformat()} — {len(day)} {noun}{where}"]
    if branches:
        lines.append(f"Branches: {', '.join(branches)}")
    if refs:
        lines.append("Refs: " + ", ".join(f"#{n}" for n in refs))
    return tuple(lines)


def cross_scope_breadcrumbs(lore_root: Path, current_wiki: str) -> list[str]:
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


def project_note_for_repo(wiki: Path, repo: str) -> dict | None:
    """Find a project note whose filename or frontmatter matches the repo.

    Returns a dict with {name, description, path} or None.
    """
    catalog = wiki_catalog(wiki)
    if catalog is None:
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


# ---------------------------------------------------------------------------
# Banner
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
    pending_chip: str | None = None
    #: Last-active-day recap off the transcript ledger (≤3 lines).
    recap: tuple[str, ...] = ()


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
    project_entry = project_note_for_repo(wiki, repo) if repo else None
    pending_chip = pending_verdict_chip(wiki) or None
    # ``<lore_root>/wiki/<name>`` is the fixed vault layout, so the ledger
    # is reachable without threading lore_root through every caller.
    recap = last_active_day_recap(wiki.parent.parent)
    return SessionFacts(
        wiki_name=wiki.name,
        repo=repo,
        scope=scope,
        project_entry=project_entry,
        pending_chip=pending_chip,
        recap=recap,
    )


def render_session_banner(facts: SessionFacts) -> str:
    """Format a SessionStart banner from collected facts.

    Ambient-minimum shape: status line (no gh fetch), optional Focus
    block, the last-active-day recap read off the transcript ledger,
    freshness lines only on positive evidence, and the single collapsed
    directive as a postscript — so users see what Lore did *first* and
    the rule re-assertion second.

    The recap replaced the last-session note hints. Continuity comes from
    the transcript ledger, which costs no LLM call to write.
    """
    injected_bits: list[str] = []
    if facts.scope:
        injected_bits.append(facts.scope)
    elif facts.project_entry is not None:
        injected_bits.append(f"[[{facts.project_entry['name']}]]")
    if facts.recap:
        # Chip form of the recap's first line — the block below carries
        # the detail; the chip only has to say "there is a last day".
        injected_bits.append(facts.recap[0].split(" — ")[0].lower())
    if facts.pending_chip:
        injected_bits.append(facts.pending_chip)
    status_line = f"lore {lore_version()}: active" + (
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

    if facts.recap:
        parts.extend(facts.recap)
        parts.append("")

    parts.extend(load_directive_lines())

    return "\n".join(parts)


def session_start_from_lore(
    cwd: str,
    config: tuple[Path, dict],
    wiki_root: Path,
) -> str | None:
    """Build SessionStart output from a route block (wiki + scope).

    Returns ``None`` if the config is unusable (wiki missing) so the
    caller can fall through to a clear "no scope" message.
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


def session_start_text(cwd: str | None) -> str:
    """Build the SessionStart context block for the route at ``cwd``.

    A repo gets a wiki from a scope in the person's config (ADR 0016). Without
    one, the banner names ``lore setup scope`` instead of guessing.
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
            "Run `lore setup`, or set $LORE_ROOT."
        )

    if cwd:
        from lore_core.session import _resolve_attach_block
        cfg = _resolve_attach_block(Path(cwd))
        if cfg is not None:
            v2 = session_start_from_lore(cwd, cfg, wiki_root)
            if v2 is not None:
                return v2

    return (
        "lore: no scope routes this repo to a wiki. "
        "Run `lore setup scope` to add one."
    )


def pre_compact_text(cwd: str | None) -> str:
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


def render_project_orientation(scope: Scope, wiki_root: Path) -> str | None:
    """Read the project orientation note for ``scope`` and return a
    formatted block for SessionStart context injection.

    Lookup order (dual-mode tolerance):
      1. ``projects/<slug>/<slug>.md`` (folder layout, post-migration)
      2. ``projects/<slug>.md``        (legacy flat)

    Slug = the repo's folder name (a folder scope covers many repos), then
    the scope's last colon-separated segment (``ccat:data-center:ops-db``
    → ``ops-db``). The first slug with a note wins. Frontmatter is stripped.
    Body is capped at :data:`ORIENTATION_BUDGET_CHARS`.

    Only the short orientation auto-loads; concepts/decisions/plans/sessions
    stay pull-on-demand.

    Returns None when no orientation note exists or the wiki root is missing.
    """
    if not wiki_root.exists() or not scope:
        return None
    slugs = [scope.claude_md_path.parent.name]
    if scope.scope:
        slugs.append(scope.scope.rsplit(":", 1)[-1])
    # Defense-in-depth: only allow conservative slug shapes, so no
    # path-traversal segment reaches the ``Path()`` join below.
    slugs = [s for s in dict.fromkeys(slugs) if s and re.fullmatch(r"[A-Za-z0-9._-]+", s)]
    wiki_path = wiki_root / scope.wiki
    candidates = [
        path
        for slug in slugs
        for path in (
            wiki_path / "projects" / slug / f"{slug}.md",
            wiki_path / "projects" / f"{slug}.md",
        )
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
            header = f"## Project: [[{path.stem}]]\n"
            return header + body
    return None


# ---------------------------------------------------------------------------
# Pre-banner world reads
# ---------------------------------------------------------------------------


def maybe_auto_pull_for_scope(scope: Scope, lore_root: Path) -> str | None:
    """Fast-forward this scope's wiki repo from origin if config opts in.

    Returns a one-line user-facing warning when the pull was skipped for
    a reason the user should know about (dirty tree, diverged history),
    or ``None`` for clean / silent outcomes (already in sync, no remote,
    pull succeeded). The fetch needs the network, so the SessionStart hook
    never calls this inline: :func:`record_auto_pull` runs it in a detached
    child and the next banner shows the recorded warning.
    """
    return _auto_pull_wiki(scope.wiki, lore_root)


def _auto_pull_wiki(wiki: str, lore_root: Path) -> str | None:
    from lore_core.git_sync import SyncStatus, auto_pull
    from lore_core.wiki_config import load_wiki_config

    wiki_dir = lore_root / "wiki" / wiki
    if not wiki_dir.exists():
        return None
    cfg = load_wiki_config(wiki_dir)
    if not cfg.git.auto_pull:
        return None

    result = auto_pull(wiki_dir)
    if result.status is SyncStatus.SKIPPED_DIRTY:
        return f"› wiki [[{wiki}]] has uncommitted changes — auto-pull skipped"
    if result.status is SyncStatus.SKIPPED_DIVERGED:
        return f"› wiki [[{wiki}]] diverged from origin — `git pull` manually"
    return None


def _auto_pull_record_path(lore_root: Path, wiki: str) -> Path:
    return lore_root / ".lore" / "auto-pull" / f"{wiki}.json"


def record_auto_pull(lore_root: Path, wiki: str) -> None:
    """Pull *wiki* from origin and record the banner warning the pull earned.

    Runs in the detached child the SessionStart hook spawns, so a slow or
    dead remote never holds up the banner. The next session start reads the
    record back through :func:`recorded_auto_pull_warning`.
    """
    from lore_core.io import atomic_write_text

    warning: str | None = None
    try:
        warning = _auto_pull_wiki(wiki, lore_root)
    finally:
        # A pull that raised records no warning, so a stale one from an
        # earlier session does not outlive the attempt.
        path = _auto_pull_record_path(lore_root, wiki)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"warning": warning}))


def recorded_auto_pull_warning(lore_root: Path, wiki: str) -> str | None:
    """Return the warning the last background pull of *wiki* recorded, if any."""
    try:
        data = json.loads(_auto_pull_record_path(lore_root, wiki).read_text())
    except (OSError, ValueError):
        return None
    warning = data.get("warning") if isinstance(data, dict) else None
    return warning if isinstance(warning, str) else None


def maybe_auto_push_for_scope(scope: Scope, lore_root: Path) -> SyncResult | None:
    """Push this scope's wiki repo at the session boundary if config opts in.

    Returns the sync result, or ``None`` when the wiki directory is
    missing or the config opts out. Unlike :func:`maybe_auto_pull_for_scope`
    this hands back the result instead of a banner line: no banner renders
    at a session boundary, and the next SessionStart already tells the user
    about a diverged wiki through the pull.

    No LLM client is passed. A note both machines changed therefore ends
    in ``MERGE_BLOCKED`` with the working tree handed back clean, and the
    user resolves it with git.
    """
    from lore_core.git_sync import auto_push
    from lore_core.wiki_config import load_wiki_config

    wiki_dir = lore_root / "wiki" / scope.wiki
    if not wiki_dir.exists():
        return None
    cfg = load_wiki_config(wiki_dir)
    if not cfg.git.auto_push:
        return None
    return auto_push(wiki_dir)


def render_capture_state_block(
    lore_root: Path,
    scope: Scope,
    cwd: Path,
    *,
    probe: bool = False,
) -> str:
    """Render the capture-state breadcrumb plus the drain and cross-scope lines.

    Returns the block to append to the banner, or "" when nothing is worth
    saying. Presentation only — no cursor advances, no writes.
    """
    from lore_core.breadcrumb import BannerContext, render_banner
    from lore_core.wiki_config import load_wiki_config

    wiki_root = get_wiki_root()
    if not wiki_root.exists():
        return ""

    wiki_cfg = load_wiki_config(lore_root / "wiki" / scope.wiki)

    # Note count is decoration; a catalog with an unexpected shape must not
    # cost us the banner.
    note_count = 0
    try:
        catalog = wiki_catalog(wiki_root / scope.wiki)
        if catalog:
            note_count = catalog.get("stats", {}).get("total_notes", 0)
    except (KeyError, TypeError, AttributeError):
        pass

    from datetime import UTC, datetime

    out = ""
    banner = render_banner(
        BannerContext(
            lore_root=lore_root,
            scope=scope,
            wiki_config=wiki_cfg,
            now=datetime.now(tz=UTC),
            note_count=note_count,
        )
    )
    if banner is not None:
        out += "\n\n" + banner

    try:
        cross = cross_scope_breadcrumbs(lore_root, scope.wiki)
        if cross:
            out += "\n" + "\n".join(cross)
    except (OSError, json.JSONDecodeError):
        pass

    return out
