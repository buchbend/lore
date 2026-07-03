"""Curator hygiene passes — frontmatter-only, deterministic vault upkeep.

Runs on the retained ``lore curator [--wiki] [--apply]`` command (and the
``/lore:curator`` skill). Never touches note bodies. Passes:

  - **supersession** — ``A supersedes [[B]]`` → write ``superseded_by: [[A]]``
    on B (only the relation; ``status:`` is not touched).
  - **implements** — process session ``implements:`` back-links: stamp
    ``implemented_by`` / ``implemented_at`` and drop ``draft:`` on the target.
  - **git_backfill** — fill missing ``created`` / ``last_reviewed`` from
    ``git log --follow``.
  - **team_mode_hint** — advise creating ``_users.yml`` when a solo wiki has
    grown multiple git authors.
  - **staleness** — a no-op: staleness is positive-evidence-only at read time
    (see :mod:`lore_core.freshness`); age alone never flags.

Writes ``_review.md`` per wiki for SessionStart to surface. Writes are
mtime-guarded — a note edited mid-run (e.g. open in Obsidian) is skipped
rather than clobbered.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lore_core.config import get_lore_root
from lore_core.git import is_obsidian_holding
from lore_core.identity import distinct_git_authors, team_mode_recommended
from lore_core.io import atomic_write_text
from lore_core.lint import discover_notes, discover_wikis
from lore_core.run_log import RunLogger
from lore_core.schema import parse_frontmatter
from lore_core.schema import split_frontmatter as _split_frontmatter
from rich.console import Console

console = Console()


# ---------------------------------------------------------------------------
# Actions + pass-protocol scaffold
# ---------------------------------------------------------------------------


@dataclass
class CuratorAction:
    kind: str
    # One of: "review_stale" | "mark_superseded" | "implements" | "backfill_git"
    # An empty `patch` marks the action as review-only (surfaced in
    # `_review.md` for interactive resolution). A `None` value in the
    # patch removes the corresponding frontmatter key.
    path: Path
    reason: str
    patch: dict


@dataclass
class CuratorReport:
    wiki: str
    actions: list[CuratorAction] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hygiene pass protocol — one wiki-walk + action-merge scaffold for all six
# Curator-C hygiene passes (action-producing and hint-producing alike).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PassContext:
    """Shared inputs passed to every hygiene pass."""

    today: date


@dataclass(frozen=True)
class PassResult:
    """Output bundle — passes may produce actions, hints, or both."""

    actions: list[CuratorAction] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HygienePass:
    """One curator hygiene pass — a named, frontmatter-only wiki walk."""

    name: str
    run: Callable[[Path, PassContext], PassResult]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUPERSEDES_RE = re.compile(
    r"supersedes?\s+\[\[([^\]]+)\]\]",
    re.IGNORECASE,
)


def _parse_implements_entry(entry: str) -> tuple[str, str, str | None]:
    """Parse an `implements:` frontmatter entry.

    Under status-vocabulary-minimalism the only frontmatter-writing
    marker is `:superseded-by:`. `:partial` and `:abandoned` remain
    parseable for session-note documentation but produce no curator
    frontmatter effect.

    Returns `(slug, kind, successor)`:
      - `my-concept`                          → (slug, "implements", None)
      - `my-concept:partial`                  → (slug, "partial", None)
      - `my-concept:abandoned`                → (slug, "abandoned", None)
      - `my-concept:superseded-by:other-slug` → (slug, "superseded", other-slug)
    """
    if ":superseded-by:" in entry:
        slug, _, rest = entry.partition(":superseded-by:")
        return (slug.strip(), "superseded", rest.strip() or None)
    if ":" in entry:
        slug, _, marker = entry.partition(":")
        marker = marker.strip()
        if marker in ("partial", "abandoned"):
            return (slug.strip(), marker, None)
    return (entry.strip(), "implements", None)


def _git_first_commit_date(repo: Path, rel_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "log", "--follow", "--diff-filter=A", "--format=%cs", "--", rel_path],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if lines else None


def _git_last_commit_date(repo: Path, rel_path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "log", "--follow", "-n", "1", "--format=%cs", "--", rel_path],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def _apply_patch(text: str, patch: dict) -> str:
    """Apply a frontmatter patch, preserving existing YAML ordering where possible.

    Patch semantics:
      - `key: value` — upsert.
      - `key: None` — remove the key (sentinel, distinct from YAML null).

    Simple approach: parse, merge, re-serialize with yaml.safe_dump.
    """
    import yaml

    split = _split_frontmatter(text)
    if split is None:
        # No frontmatter — create one (filter out removal sentinels)
        fm = {k: v for k, v in patch.items() if v is not None}
        body = text
    else:
        fm_block, body = split
        fm = yaml.safe_load(fm_block) or {}
        for key, value in patch.items():
            if value is None:
                fm.pop(key, None)
            else:
                fm[key] = value
    new_fm = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip() + "\n"
    return f"---\n{new_fm}---\n{body}"


# ---------------------------------------------------------------------------
# Curation passes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Curation passes
# ---------------------------------------------------------------------------


def _pass_staleness(wiki_path: Path, today: date, threshold: int) -> list[CuratorAction]:
    """No-op since PRD #65 (positive-evidence-only staleness).

    The legacy 90-day age rule is incompatible with the read-time
    freshness model in :mod:`lore_core.freshness`. Age alone never
    flags a note now — staleness requires a named cause (authored
    marker or broken wikilink). The function survives as a hook for
    future positive-evidence aggregation in Curator C (e.g., rolling
    up orphan-flagged notes into the report). Arguments are accepted
    but unused.
    """
    return []


def _pass_supersession(wiki_path: Path) -> list[CuratorAction]:
    """When note A says `supersedes [[B]]`, write `superseded_by: [[A]]` on B.

    Per status-vocabulary-minimalism: only the `superseded_by:`
    relation is written; `status:` is not touched.
    """
    actions: list[CuratorAction] = []
    # Build filename → path map for quick lookup
    by_name: dict[str, Path] = {}
    for fpath in discover_notes(wiki_path):
        by_name[fpath.stem] = fpath

    for fpath in discover_notes(wiki_path):
        text = fpath.read_text(errors="replace")
        for match in _SUPERSEDES_RE.finditer(text):
            target = match.group(1).split("|")[0].strip()
            target_path = by_name.get(target)
            if target_path is None:
                continue
            target_fm = parse_frontmatter(target_path.read_text(errors="replace"))
            expected = f"[[{fpath.stem}]]"
            if target_fm.get("superseded_by") == expected:
                continue
            actions.append(
                CuratorAction(
                    kind="mark_superseded",
                    path=target_path,
                    reason=f"superseded by [[{fpath.stem}]]",
                    patch={"superseded_by": expected},
                )
            )
    return actions


def _pass_implements(wiki_path: Path) -> list[CuratorAction]:
    """Process `implements:` session-note frontmatter.

    Per status-vocabulary-minimalism:
      - `implements: slug` → if target has `draft: true`, drop it; stamp
        `implemented_at:` + `implemented_by:` back-links.
      - `implements: slug:superseded-by:other` → set `superseded_by:
        [[other]]` on target.
      - `implements: slug:partial` / `slug:abandoned` → no frontmatter
        effect (marker is documentation in the session note only).

    If the target is already canonical (no `draft:` flag), a plain
    `implements:` entry has no frontmatter effect — it remains a pure
    back-link in the session note.

    Idempotent: skips targets where the required state is already in
    place. Targets that can't be resolved by slug are silently skipped.
    """
    actions: list[CuratorAction] = []

    by_name: dict[str, Path] = {}
    for fpath in discover_notes(wiki_path):
        by_name[fpath.stem] = fpath

    sessions_dir = wiki_path / "sessions"
    if not sessions_dir.exists():
        return actions

    for session in sorted(sessions_dir.rglob("*.md")):
        text = session.read_text(errors="replace")
        fm = parse_frontmatter(text)
        if fm.get("type") != "session":
            continue
        implements = fm.get("implements") or []
        if not implements:
            continue
        session_slug = session.stem
        session_date = str(fm.get("created") or "")

        for raw in implements:
            slug, kind, successor = _parse_implements_entry(str(raw))
            target = by_name.get(slug)
            if target is None:
                continue
            target_fm = parse_frontmatter(target.read_text(errors="replace"))
            expected_by = f"[[{session_slug}]]"

            if kind == "superseded":
                expected_sb = f"[[{successor}]]" if successor else None
                if not expected_sb:
                    continue
                if target_fm.get("superseded_by") == expected_sb:
                    continue
                patch: dict = {"superseded_by": expected_sb}
                actions.append(
                    CuratorAction(
                        kind="mark_superseded",
                        path=target,
                        reason=f"superseded by [[{successor}]] via [[{session_slug}]]",
                        patch=patch,
                    )
                )
                continue

            if kind in ("partial", "abandoned"):
                # Marker is session-note documentation only; no target frontmatter change.
                continue

            # Default case: `implements: slug`.
            was_draft = target_fm.get("draft") is True
            already_stamped = target_fm.get("implemented_by") == expected_by and (
                not session_date or target_fm.get("implemented_at") == session_date
            )
            if not was_draft and already_stamped:
                continue
            if not was_draft and target_fm.get("implemented_by") == expected_by:
                # Canonical target already linked to this session → no-op.
                continue

            patch = {"implemented_by": expected_by}
            if session_date:
                patch["implemented_at"] = session_date
            if was_draft:
                patch["draft"] = None  # sentinel: _apply_patch removes the key
            actions.append(
                CuratorAction(
                    kind="implements",
                    path=target,
                    reason=(
                        f"promoted from draft by [[{session_slug}]]"
                        if was_draft
                        else f"back-link from [[{session_slug}]]"
                    ),
                    patch=patch,
                )
            )
    return actions


def _pass_team_mode_hint(wiki_path: Path) -> list[str]:
    """Check whether the wiki has outgrown solo mode.

    Returns a list of human-readable hints for `_review.md`. Does not
    create `_users.yml` — that's opt-in by the user. Solo wikis and
    already-team wikis produce no hint.
    """
    if not team_mode_recommended(wiki_path):
        return []
    authors = sorted(distinct_git_authors(wiki_path))
    return [
        "Team-mode activation recommended: "
        f"{len(authors)} distinct authors in git log "
        f"({', '.join(authors[:5])}{'…' if len(authors) > 5 else ''}), "
        "but no `_users.yml` yet. "
        "Create `_users.yml` to enable identity aliasing and session sharding.",
    ]


def _pass_git_backfill(wiki_path: Path) -> list[CuratorAction]:
    actions: list[CuratorAction] = []
    for fpath in discover_notes(wiki_path):
        text = fpath.read_text(errors="replace")
        fm = parse_frontmatter(text)
        rel = str(fpath.relative_to(wiki_path))
        patch: dict = {}
        if not fm.get("created"):
            first = _git_first_commit_date(wiki_path, rel)
            if first:
                patch["created"] = first
        if not fm.get("last_reviewed"):
            last = _git_last_commit_date(wiki_path, rel)
            if last:
                patch["last_reviewed"] = last
        if patch:
            actions.append(
                CuratorAction(
                    kind="backfill_git",
                    path=fpath,
                    reason=f"filled {','.join(patch)} from git log",
                    patch=patch,
                )
            )
    return actions


# ---------------------------------------------------------------------------
# Pass-protocol adapters + registry
# ---------------------------------------------------------------------------


def _run_staleness(wiki_path: Path, ctx: PassContext) -> PassResult:
    return PassResult(actions=_pass_staleness(wiki_path, ctx.today, 0))


def _run_supersession(wiki_path: Path, ctx: PassContext) -> PassResult:
    return PassResult(actions=_pass_supersession(wiki_path))


def _run_implements(wiki_path: Path, ctx: PassContext) -> PassResult:
    return PassResult(actions=_pass_implements(wiki_path))


def _run_git_backfill(wiki_path: Path, ctx: PassContext) -> PassResult:
    return PassResult(actions=_pass_git_backfill(wiki_path))


def _run_team_mode_hint(wiki_path: Path, ctx: PassContext) -> PassResult:
    return PassResult(hints=_pass_team_mode_hint(wiki_path))


HYGIENE_PASSES: list[HygienePass] = [
    HygienePass("staleness", _run_staleness),
    HygienePass("supersession", _run_supersession),
    HygienePass("implements", _run_implements),
    HygienePass("git_backfill", _run_git_backfill),
    HygienePass("team_mode_hint", _run_team_mode_hint),
]

# ---------------------------------------------------------------------------
# Write path (safe — mtime guard)
# ---------------------------------------------------------------------------


def _apply_safely(action: CuratorAction) -> tuple[bool, str]:
    """Apply one action with a pre/post mtime check. Returns (applied, reason)."""
    if not action.patch:
        # Review-only action (e.g. stale flag) — surfaced in _review.md, no write.
        return (True, "review-only")
    before = action.path.stat().st_mtime
    text_before = action.path.read_text(errors="replace")
    new_text = _apply_patch(text_before, action.patch)

    # Re-check mtime right before write
    now = action.path.stat().st_mtime
    if now != before:
        return (
            False,
            f"file changed on disk between read and write (mtime {before} → {now}); aborted",
        )
    atomic_write_text(action.path, new_text)
    return (True, "applied")


# ---------------------------------------------------------------------------
# Orchestrator — the retained `lore curator` hygiene run
# ---------------------------------------------------------------------------


def run_hygiene(
    wiki_filter: str | None = None,
    dry_run: bool = True,
    *,
    run_id: str | None = None,
) -> list[CuratorReport]:
    """Run the deterministic hygiene passes over each wiki.

    Passes are frontmatter-only and never touch note bodies. Writes
    ``_review.md`` per wiki. ``dry_run`` (the default) writes nothing;
    ``--apply`` performs the mtime-guarded writes.
    """
    import contextlib
    from datetime import UTC
    from datetime import datetime as _dt

    wikis = discover_wikis(wiki_filter)
    reports: list[CuratorReport] = []
    today = date.today()
    now = _dt.now(UTC)
    rid = run_id or now.strftime("%Y-%m-%dT%H-%M-%S")

    try:
        lore_root = get_lore_root()
    except Exception:
        lore_root = None

    logger: RunLogger | None = None
    if lore_root is not None:
        logger = RunLogger(
            lore_root,
            trigger="hook",
            role="c",
            config_snapshot={"wiki_filter": wiki_filter, "dry_run": dry_run},
            dry_run=dry_run,
            run_id=rid,
        )

    def _emit(record_type: str, **fields: object) -> None:
        if logger is not None:
            logger.emit(record_type, **fields)

    ctx_mgr = logger if logger is not None else contextlib.nullcontext()

    with ctx_mgr:
        for wiki_path in wikis:
            _emit("wiki-start", wiki=wiki_path.name)

            if is_obsidian_holding(wiki_path) and not dry_run:
                console.print(
                    f"[yellow]Warning:[/yellow] Obsidian appears active in "
                    f"{wiki_path}. Proceeding — but if you have mid-edit "
                    "buffers, close them first."
                )

            report = CuratorReport(wiki=wiki_path.name)
            pass_ctx = PassContext(today=today)
            for pass_def in HYGIENE_PASSES:
                result = pass_def.run(wiki_path, pass_ctx)
                report.actions.extend(result.actions)
                report.hints.extend(result.hints)
            reports.append(report)

        for report in reports:
            _print_report(report, dry_run)

        if not dry_run:
            for report in reports:
                for action in report.actions:
                    ok, reason = _apply_safely(action)
                    if ok:
                        _emit(
                            "action-applied",
                            wiki=report.wiki,
                            kind=action.kind,
                            path=str(action.path),
                            reason=action.reason,
                        )
                    else:
                        report.skipped.append((action.path, reason))
                        _emit(
                            "action-skipped",
                            wiki=report.wiki,
                            path=str(action.path),
                            reason=reason,
                        )
                        console.print(f"  [red]skipped[/red] {action.path.name}: {reason}")

        for report in reports:
            wiki_path = next(w for w in wikis if w.name == report.wiki)
            _write_review(wiki_path, report)

    return reports


def _print_report(report: CuratorReport, dry_run: bool) -> None:
    verb = "would" if dry_run else "will"
    console.print(f"\n[bold cyan]wiki/{report.wiki}/[/bold cyan]")
    if not report.actions:
        console.print("  [green]Nothing to do.[/green]")
        return
    by_kind: dict[str, list[CuratorAction]] = {}
    for action in report.actions:
        by_kind.setdefault(action.kind, []).append(action)
    for kind, actions in sorted(by_kind.items()):
        console.print(f"  {verb} {kind} ({len(actions)}):")
        for a in actions[:5]:
            console.print(f"    {a.path.name} — {a.reason}")
        if len(actions) > 5:
            console.print(f"    … and {len(actions) - 5} more")


def _write_review(wiki_path: Path, report: CuratorReport) -> None:
    """Write `_review.md` summarizing curator findings for SessionStart."""
    if not report.actions and not report.hints:
        # Clear any old review file
        review = wiki_path / "_review.md"
        if review.exists():
            atomic_write_text(review, "# Curator review\n\nNothing pending.\n")
        return
    lines = [
        f"# Curator review — {wiki_path.name}",
        "",
        f"Generated {date.today().isoformat()}. "
        "Review and resolve; these were flagged by the curator.",
        "",
    ]
    by_kind: dict[str, list[CuratorAction]] = {}
    for action in report.actions:
        by_kind.setdefault(action.kind, []).append(action)
    for kind, actions in sorted(by_kind.items()):
        lines.append(f"## {kind} ({len(actions)})")
        lines.append("")
        for a in actions:
            lines.append(f"- `{a.path.name}` — {a.reason}")
        lines.append("")
    if report.hints:
        lines.append("## hints")
        lines.append("")
        for hint in report.hints:
            lines.append(f"- {hint}")
        lines.append("")
    atomic_write_text(wiki_path / "_review.md", "\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
