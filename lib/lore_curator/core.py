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

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rich.console import Console

from lore_core.git import is_obsidian_holding
from lore_core.io import atomic_write_text
from lore_core.lint import STALENESS_DAYS, discover_notes, discover_wikis
from lore_core.schema import parse_frontmatter

console = Console()


# ---------------------------------------------------------------------------
# Actions the curator can take — each is a dry-run-printable record
# ---------------------------------------------------------------------------


@dataclass
class CuratorAction:
    kind: str  # "mark_stale" | "mark_superseded" | "backfill_created" | "backfill_last_reviewed"
    path: Path
    reason: str
    patch: dict  # frontmatter fields to set


@dataclass
class CuratorReport:
    wiki: str
    actions: list[CuratorAction] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SUPERSEDES_RE = re.compile(
    r"supersedes?\s+\[\[([^\]]+)\]\]",
    re.IGNORECASE,
)


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

    Simple approach: parse, merge, re-serialize with yaml.safe_dump.
    """
    import yaml

    split = _split_frontmatter(text)
    if split is None:
        # No frontmatter — create one
        fm = patch
        body = text
    else:
        fm_block, body = split
        fm = yaml.safe_load(fm_block) or {}
        fm.update(patch)
    new_fm = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip() + "\n"
    return f"---\n{new_fm}---\n{body}"


# ---------------------------------------------------------------------------
# Curation passes
# ---------------------------------------------------------------------------


def _pass_staleness(wiki_path: Path, today: date, threshold: int) -> list[CuratorAction]:
    actions: list[CuratorAction] = []
    for fpath in discover_notes(wiki_path):
        text = fpath.read_text(errors="replace")
        fm = parse_frontmatter(text)
        if fm.get("status") != "active":
            continue
        lr = fm.get("last_reviewed")
        if not lr:
            continue
        try:
            lr_date = date.fromisoformat(str(lr))
        except (ValueError, TypeError):
            continue
        if (today - lr_date).days > threshold:
            actions.append(
                CuratorAction(
                    kind="mark_stale",
                    path=fpath,
                    reason=f"last_reviewed {lr} (> {threshold} days)",
                    patch={"status": "stale"},
                )
            )
    return actions


def _pass_supersession(wiki_path: Path) -> list[CuratorAction]:
    """When note A says `supersedes [[B]]`, mark B as superseded_by A."""
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
            if target_fm.get("status") == "superseded":
                continue
            actions.append(
                CuratorAction(
                    kind="mark_superseded",
                    path=target_path,
                    reason=f"superseded by [[{fpath.stem}]]",
                    patch={
                        "status": "superseded",
                        "superseded_by": f"[[{fpath.stem}]]",
                    },
                )
            )
    return actions


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
        report.actions.extend(_pass_git_backfill(wiki_path))
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
    if not report.actions:
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
    atomic_write_text(wiki_path / "_review.md", "\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lore-curator")
    parser.add_argument("--wiki", help="Scope to one wiki")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this, runs dry.",
    )
    parser.add_argument(
        "--stale-threshold",
        type=int,
        default=STALENESS_DAYS,
        help=f"Days after which `status: active` notes become stale (default {STALENESS_DAYS})",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable summary")
    args = parser.parse_args(argv)

    reports = run_curator(
        wiki_filter=args.wiki,
        dry_run=not args.apply,
        stale_threshold=args.stale_threshold,
    )

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "wiki": r.wiki,
                        "actions": [
                            {"kind": a.kind, "path": str(a.path), "reason": a.reason}
                            for a in r.actions
                        ],
                        "skipped": [
                            {"path": str(p), "reason": reason} for p, reason in r.skipped
                        ],
                    }
                    for r in reports
                ],
                indent=2,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
