"""Lore curator — per-wiki maintenance that keeps auto-inject trustworthy.

What the curator does (frontmatter-only edits):

    1. Flag `status: active` + `last_reviewed > 90d` as `status: stale`
    2. Detect `supersedes [[X]]` in decision notes; mark X as
       superseded and backlink.
    3. Backfill missing `last_reviewed` / `created` from `git log --follow`.
    4. Writes a `_review.md` summary the hook can surface next session.

Safety:
    - Never edits note bodies without explicit user approval.
    - Mtime guard: reads mtime before patch, re-reads file and aborts
      if it changed mid-patch (Obsidian-open race).
    - Warns if Obsidian appears to hold the vault.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lore_core.git import is_obsidian_holding
from lore_core.identity import distinct_git_authors, team_mode_recommended
from lore_core.io import atomic_write_text
from lore_core.lint import STALENESS_DAYS, discover_notes, discover_wikis
from lore_core.schema import compute_lifecycle, parse_frontmatter
from rich.console import Console

# ---------------------------------------------------------------------------
# v1 → v2 session-note migration for `## Open items`
# ---------------------------------------------------------------------------

_OPEN_ITEMS_HEADING = "## Open items"
_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


def extract_open_items(text: str) -> list[str]:
    """Return bullet items (without `- ` prefix) under `## Open items`.

    Returns [] if the heading is absent or the section body has no bullets.
    `- None` / `- _None_` placeholders are treated as empty.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == _OPEN_ITEMS_HEADING:
            start = i
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _SECTION_HEADING_RE.match(lines[j])
        if m and m.group(1).strip() != "Open items":
            end = j
            break
    out: list[str] = []
    for line in lines[start + 1 : end]:
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if body.lower() in ("none", "_none_"):
            continue
        out.append(body)
    return out


def _bump_schema_version_to_2(fm_block: str) -> str:
    """Return fm_block with schema_version bumped (or added) to 2."""
    lines = fm_block.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("schema_version:"):
            lines[i] = "schema_version: 2"
            return "\n".join(lines)
    return "schema_version: 2\n" + fm_block


def _split_body_by_open_items(body: str) -> tuple[str, str, str]:
    """Return (before, open_items_block, after).

    `before` ends right before the `## Open items` heading.
    `open_items_block` is the full `## Open items` section including heading.
    `after` is everything from the next `## ` heading onwards.
    If `## Open items` is absent, returns (body, "", "").
    """
    lines = body.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == _OPEN_ITEMS_HEADING:
            start = i
            break
    if start is None:
        return body, "", ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("## ") and stripped != _OPEN_ITEMS_HEADING:
            end = j
            break
    before = "".join(lines[:start])
    section = "".join(lines[start:end])
    after = "".join(lines[end:])
    return before, section, after


def migrate_open_items(
    text: str,
    decisions: list[tuple[str, str | None]],
) -> str:
    """Rewrite a v1 session note to v2.

    - Bumps `schema_version` to 2 in the frontmatter.
    - Replaces `## Open items` with `## Issues touched` + `## Loose ends`.
    - `decisions[i]` is applied to the i-th bullet returned by
      `extract_open_items`. Each decision is `(choice, issue_number)`:
        * `("issue", "#47")`    → `## Issues touched` as `- #47 <text>`
        * `("issue", None)`     → `## Issues touched` as `- <text> (needs issue)`
        * `("loose_end", _)`    → `## Loose ends` as `- <text>`
        * `("resolved", _)`     → dropped
    - Idempotent: re-running produces the same output (no `## Open items`
      left to extract the second time).

    Bullets without a matching decision default to `("loose_end", None)`.
    """
    items = extract_open_items(text)

    # Pad decisions to match items length.
    padded = list(decisions) + [("loose_end", None)] * (len(items) - len(decisions))

    issues_touched: list[str] = []
    loose_ends: list[str] = []
    for item, (choice, issue_ref) in zip(items, padded, strict=False):
        if choice == "issue":
            if issue_ref:
                issues_touched.append(f"- {issue_ref} {item}")
            else:
                issues_touched.append(f"- {item} (needs issue)")
        elif choice == "loose_end":
            loose_ends.append(f"- {item}")
        elif choice == "resolved":
            continue
        else:
            loose_ends.append(f"- {item}")

    issues_block_lines = ["## Issues touched", ""]
    issues_block_lines.extend(issues_touched or ["- _None_"])
    issues_block_lines.append("")
    loose_block_lines = ["## Loose ends", ""]
    loose_block_lines.extend(loose_ends or ["- _None_"])
    loose_block_lines.append("")
    replacement = "\n".join(issues_block_lines + loose_block_lines)

    # Split frontmatter.
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    fm_block = text[4:end]
    body = text[end + 4 :].lstrip("\n")

    fm_block = _bump_schema_version_to_2(fm_block)

    before, old_section, after = _split_body_by_open_items(body)
    if old_section:
        new_body = before + replacement
        if after:
            if not new_body.endswith("\n"):
                new_body += "\n"
            new_body += after
    else:
        new_body = body

    return f"---\n{fm_block}\n---\n\n{new_body.lstrip()}"

console = Console()


# ---------------------------------------------------------------------------
# Actions the curator can take — each is a dry-run-printable record
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


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[4:end], text[end + 4 :].lstrip("\n")


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


def _pass_staleness(wiki_path: Path, today: date, threshold: int) -> list[CuratorAction]:
    """Flag canonical notes whose `last_reviewed` exceeds `threshold` days.

    Review-only: the action carries an empty patch. The user resolves
    each flagged note interactively (bump `last_reviewed`, add
    `superseded_by:`, or delete). `_apply_safely` treats empty patches
    as a no-op.
    """
    actions: list[CuratorAction] = []
    for fpath in discover_notes(wiki_path):
        text = fpath.read_text(errors="replace")
        fm = parse_frontmatter(text)
        if fm.get("type") == "session":
            continue
        if compute_lifecycle(fm) != "canonical":
            continue
        lr = fm.get("last_reviewed")
        if not lr:
            continue
        try:
            lr_date = date.fromisoformat(str(lr))
        except (ValueError, TypeError):
            continue
        days = (today - lr_date).days
        if days > threshold:
            actions.append(
                CuratorAction(
                    kind="review_stale",
                    path=fpath,
                    reason=f"last_reviewed {lr} ({days} days ago, > {threshold})",
                    patch={},
                )
            )
    return actions


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
            already_stamped = (
                target_fm.get("implemented_by") == expected_by
                and (not session_date or target_fm.get("implemented_at") == session_date)
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


def run_curator(
    wiki_filter: str | None = None,
    dry_run: bool = True,
    stale_threshold: int = STALENESS_DAYS,
) -> list[CuratorReport]:
    wikis = discover_wikis(wiki_filter)
    reports: list[CuratorReport] = []
    today = date.today()

    for wiki_path in wikis:
        if is_obsidian_holding(wiki_path) and not dry_run:
            console.print(
                f"[yellow]Warning:[/yellow] Obsidian appears active in "
                f"{wiki_path}. Proceeding — but if you have mid-edit "
                "buffers, close them first."
            )
        report = CuratorReport(wiki=wiki_path.name)
        report.actions.extend(_pass_staleness(wiki_path, today, stale_threshold))
        report.actions.extend(_pass_supersession(wiki_path))
        report.actions.extend(_pass_implements(wiki_path))
        report.actions.extend(_pass_git_backfill(wiki_path))
        report.hints.extend(_pass_team_mode_hint(wiki_path))
        reports.append(report)

    for report in reports:
        _print_report(report, dry_run)

    if not dry_run:
        for report in reports:
            for action in report.actions:
                ok, reason = _apply_safely(action)
                if not ok:
                    report.skipped.append((action.path, reason))
                    console.print(
                        f"  [red]skipped[/red] {action.path.name}: {reason}"
                    )

    # Write a review summary file per wiki so next SessionStart can surface it
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


def run_open_items_migration(
    wiki_filter: str | None = None,
    dry_run: bool = True,
) -> int:
    """Interactive v1 → v2 migration for `## Open items` session sections.

    Walks each v1 session note with a non-empty `## Open items` section
    and prompts per-bullet: issue / loose end / resolved / skip note.
    Pure rewriting logic lives in `migrate_open_items`; this is the TTY.

    Returns the count of notes migrated.
    """
    from rich.prompt import Prompt

    wikis = discover_wikis(wiki_filter)
    migrated = 0
    for wiki_path in wikis:
        sessions_dir = wiki_path / "sessions"
        if not sessions_dir.exists():
            continue
        for session in sorted(sessions_dir.rglob("*.md")):
            text = session.read_text(errors="replace")
            fm = parse_frontmatter(text)
            if fm.get("schema_version") != 1:
                continue
            items = extract_open_items(text)
            if not items:
                continue

            rel = session.relative_to(wiki_path)
            console.print(f"\n[bold cyan]{wiki_path.name}/{rel}[/bold cyan]")
            decisions: list[tuple[str, str | None]] | None = []
            for item in items:
                console.print(f"  • {item}")
                choice = Prompt.ask(
                    "    → (i)ssue / (l)oose end / (r)esolved / (s)kip note",
                    choices=["i", "l", "r", "s"],
                    default="l",
                )
                if choice == "s":
                    decisions = None
                    break
                if choice == "i":
                    ref = Prompt.ask(
                        "      issue ref (e.g. #47), blank for 'needs issue'",
                        default="",
                    )
                    decisions.append(("issue", ref.strip() or None))
                elif choice == "l":
                    decisions.append(("loose_end", None))
                elif choice == "r":
                    decisions.append(("resolved", None))

            if decisions is None:
                console.print("  [yellow]skipped (left as v1)[/yellow]")
                continue

            new_text = migrate_open_items(text, decisions)
            if dry_run:
                console.print("  [dim]would rewrite to v2 (use --apply to commit)[/dim]")
            else:
                atomic_write_text(session, new_text)
                console.print("  [green]migrated to v2[/green]")
            migrated += 1

    verb = "would migrate" if dry_run else "migrated"
    console.print()
    console.print(f"[bold]{verb} {migrated} session note(s)[/bold]")
    return migrated


import typer  # noqa: E402

from lore_cli._compat import argv_main  # noqa: E402

app = typer.Typer(
    add_completion=False,
    help="Curator — flag stale notes, propagate `implements:` status flips, etc.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def curator(
    wiki: str = typer.Option(None, "--wiki", help="Scope to one wiki."),
    apply: bool = typer.Option(
        False, "--apply", help="Actually write changes. Without this, runs dry."
    ),
    stale_threshold: int = typer.Option(
        STALENESS_DAYS,
        "--stale-threshold",
        help=f"Days after which active notes become stale (default {STALENESS_DAYS}).",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit machine-readable summary."
    ),
    migrate_open_items: bool = typer.Option(
        False,
        "--migrate-open-items",
        help="Interactive v1 → v2 migration for `## Open items` session sections.",
    ),
) -> None:
    """Run curator passes — flag stale, propagate implements, etc."""
    if migrate_open_items:
        run_open_items_migration(wiki_filter=wiki, dry_run=not apply)
        return

    reports = run_curator(
        wiki_filter=wiki,
        dry_run=not apply,
        stale_threshold=stale_threshold,
    )

    if json_out:
        print(
            json.dumps(
                {
                    "schema": "lore.curator/1",
                    "data": {
                        "dry_run": not apply,
                        "wikis": [
                            {
                                "wiki": r.wiki,
                                "actions": [
                                    {
                                        "kind": a.kind,
                                        "path": str(a.path),
                                        "reason": a.reason,
                                    }
                                    for a in r.actions
                                ],
                                "skipped": [
                                    {"path": str(p), "reason": reason}
                                    for p, reason in r.skipped
                                ],
                            }
                            for r in reports
                        ],
                    },
                },
                indent=2,
            )
        )


def _discover_wikis(lore_root: Path) -> list[str]:
    """Return sorted list of wiki directory names under lore_root/wiki/."""
    wiki_dir = lore_root / "wiki"
    if not wiki_dir.exists():
        return []
    return sorted([d.name for d in wiki_dir.iterdir() if d.is_dir()])


@app.command("run")
def run_command(
    scope: str = typer.Option(None, "--scope", help="Filter to one scope, e.g. 'mywiki:subproject'."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Classify but don't write notes or advance ledger."),
    abstract: bool = typer.Option(False, "--abstract", help="Also run Curator B (graph abstraction) after Curator A."),
    wiki: str = typer.Option(None, "--wiki", help="Limit Curator B to a single wiki (only meaningful with --abstract)."),
    trace_llm: bool = typer.Option(False, "--trace-llm", help="Capture LLM prompts/responses to runs/<id>.trace.jsonl (equivalent to LORE_TRACE_LLM=1)."),
) -> None:
    """Run Curator A — classify pending transcripts and file session notes.

    With --abstract, also runs Curator B (graph abstraction) for the specified wiki(s).
    """
    import os
    from datetime import UTC, datetime
    from pathlib import Path

    from lore_curator.curator_a import run_curator_a

    lore_root_str = os.environ.get("LORE_ROOT", "")
    if not lore_root_str:
        console.print("[red]Error:[/red] LORE_ROOT environment variable not set.")
        raise typer.Exit(1)

    lore_root = Path(lore_root_str)

    # Build scope filter if provided
    scope_obj = None
    if scope:
        from lore_core.types import Scope
        wiki_name = scope.split(":")[0] if ":" in scope else scope
        scope_obj = Scope(
            wiki=wiki_name,
            scope=scope,
            backend="none",
            claude_md_path=Path("."),
        )

    # Attempt to instantiate Anthropic client (lazy)
    anthropic_client = None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic as _anthropic
            anthropic_client = _anthropic.Anthropic(api_key=api_key)
        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] Could not instantiate Anthropic client: {exc}")
    else:
        console.print(
            "[yellow]Warning:[/yellow] ANTHROPIC_API_KEY not set — "
            "curator will skip AI classification (dry content pass only)."
        )

    # Compute effective trace_llm from flag or environment variable
    effective_trace = trace_llm or os.environ.get("LORE_TRACE_LLM") == "1"

    result = run_curator_a(
        lore_root=lore_root,
        scope=scope_obj,
        anthropic_client=anthropic_client,
        dry_run=dry_run,
        now=datetime.now(UTC),
        trigger="manual",
        trace_llm=effective_trace,
    )

    skipped_summary = ", ".join(
        f"{k}: {v}" for k, v in result.skipped_reasons.items()
    ) or "none"

    console.print(
        f"[bold]Curator A[/bold] — {result.transcripts_considered} transcript(s) considered"
    )
    console.print(f"  noteworthy: {result.noteworthy_count}")
    console.print(
        f"  new notes: {len(result.new_notes)}, merged: {len(result.merged_notes)}"
    )
    console.print(f"  skipped: {{{skipped_summary}}}")
    console.print(f"  took: {result.duration_seconds:.2f}s")

    # Run Curator B if --abstract is specified
    if abstract:
        from lore_curator.curator_b import run_curator_b

        wikis_to_process = [wiki] if wiki else _discover_wikis(lore_root)

        for wiki_name in wikis_to_process:
            b_result = run_curator_b(
                lore_root=lore_root,
                wiki=wiki_name,
                anthropic_client=anthropic_client,
                dry_run=dry_run,
                now=datetime.now(UTC),
            )

            skipped_b_summary = ", ".join(
                f"{k}: {v}" for k, v in b_result.skipped_reasons.items()
            ) or "none"

            console.print(
                f"[bold]Curator B[/bold] ({wiki_name}) — {b_result.notes_considered} note(s) considered"
            )
            console.print(f"  clusters: {b_result.clusters_formed}")
            console.print(f"  surfaces: {len(b_result.surfaces_emitted)}")
            console.print(f"  skipped: {{{skipped_b_summary}}}")
            console.print(f"  took: {b_result.duration_seconds:.2f}s")


main = argv_main(app)


if __name__ == "__main__":
    sys.exit(main())
