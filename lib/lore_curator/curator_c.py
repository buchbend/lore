"""Curator C — weekly defrag / converge / stale-flag / supersession.

The third member of the A/B/C curator triad. A writes session notes
(per-session), B extracts concept surfaces (per-day-rollover), C runs
weekly to keep the vault's frontmatter trustworthy so SessionStart
auto-injection doesn't surface stale or superseded notes.

What Curator C does (frontmatter-only edits — never touches note bodies):

    1. Flag `status: active` + `last_reviewed > 90d` as `status: stale`.
    2. Detect `supersedes [[X]]` in decision notes; mark X as superseded
       and backlink.
    3. Backfill missing `last_reviewed` / `created` from `git log --follow`.
    4. Propagate `implements:` status flips.
    5. Write a `_review.md` summary the hook can surface next session.

Cadence: weekly. Triggered from SessionStart when `now - last_curator_c
> 7d` and no global curator lock is held (see project_lore_heartbeat.md).
Currently manual-only via `lore curator`; the SessionStart trigger is
scheduled for Plan 5.

Safety:
    - Never edits note bodies without explicit user approval.
    - Mtime guard: reads mtime before patch, re-reads and aborts if the
      file changed mid-patch (Obsidian-open race).
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

from lore_core.config import get_lore_root
from lore_core.git import is_obsidian_holding
from lore_core.identity import distinct_git_authors, team_mode_recommended
from lore_core.io import atomic_write_text
from lore_core.ledger import WikiLedger
from lore_core.lint import STALENESS_DAYS, discover_notes, discover_wikis
from lore_core.run_log import RunLogger
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


def _pass_draft_promotion(
    wiki_path: Path, today: date, threshold_days: int = 14
) -> list[CuratorAction]:
    """Time-based proposal: mark long-standing drafts with
    ``promotion_candidate: true``. NEVER flips ``draft: false``.

    A note is a candidate when:
      - ``draft: true`` AND
      - ``created`` date is older than ``threshold_days`` days ago AND
      - ``promotion_candidate`` is not already set
    """
    from datetime import date as _date_t, timedelta
    actions: list[CuratorAction] = []
    cutoff = today - timedelta(days=threshold_days)

    for fpath in discover_notes(wiki_path):
        try:
            text = fpath.read_text(errors="replace")
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if fm is None:
            continue
        if fm.get("draft") is not True:
            continue
        if fm.get("promotion_candidate") is True:
            continue  # idempotent
        created = fm.get("created")
        if isinstance(created, str):
            try:
                created = _date_t.fromisoformat(created)
            except ValueError:
                continue
        if not isinstance(created, _date_t):
            continue
        # Strictly older than cutoff — boundary exclusive.
        if not (created < cutoff):
            continue

        # Patch: append promotion_candidate: true at end of frontmatter.
        actions.append(
            CuratorAction(
                path=fpath,
                kind="promote-draft",
                reason=f"draft created {(today - created).days}d ago ({created.isoformat()})",
                patch={"promotion_candidate": True},
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


# Registry for LLM-driven Curator C defrag passes. Phase B of Plan 5
# appends callables here; each callable has signature
# ``(wiki_path, *, llm_client, dry_run) -> dict[str, int]`` (summary counts).
_DEFRAG_PASSES: list = []


def _ensure_passes_registered() -> None:
    """Lazy-import pass modules so their _register() side effects fire.

    The pass modules each register themselves into _DEFRAG_PASSES on
    import. Importing them eagerly from this module would circular-
    import. Instead, we trigger them here the first time defrag runs.
    """
    import lore_curator.c_adjacent_merge  # noqa: F401
    import lore_curator.c_auto_supersede  # noqa: F401
    import lore_curator.c_orphan_links   # noqa: F401


def _run_defrag_passes(
    wiki_path,
    *,
    llm_client,
    dry_run: bool,
) -> dict[str, int]:
    """Run every registered LLM pass for one wiki; return merged summary."""
    _ensure_passes_registered()
    summary: dict[str, int] = {}
    for pass_fn in _DEFRAG_PASSES:
        counts = pass_fn(
            wiki_path, llm_client=llm_client, dry_run=dry_run
        ) or {}
        for k, v in counts.items():
            summary[k] = summary.get(k, 0) + v
    return summary


def _snapshot_wiki(wiki_path) -> dict:
    """Return {relative_path: content_str} for every .md file under wiki_path."""
    out: dict = {}
    if not wiki_path.exists():
        return out
    for p in sorted(wiki_path.rglob("*.md")):
        if p.is_file():
            try:
                out[str(p.relative_to(wiki_path))] = p.read_text(errors="replace")
            except OSError:
                continue
    return out


def run_curator_c(
    wiki_filter: str | None = None,
    dry_run: bool = True,
    stale_threshold: int = STALENESS_DAYS,
    *,
    defrag: bool = False,
    llm_client=None,
    run_id: str | None = None,
) -> list[CuratorReport]:
    """Run Curator C — hygiene by default; with ``defrag=True`` also runs
    LLM passes (adjacent-merge, auto-supersede, orphan-repair) registered
    in ``_DEFRAG_PASSES``.

    When ``defrag=True``:
      - pre-flight: abort if wiki repo has merge conflicts
      - snapshot wiki before running passes
      - run hygiene passes (pre-existing) + defrag passes
      - write a diff-log entry
      - prune old diff logs (90d retention)
      - update ``WikiLedger.last_curator_c`` on success (atomic)
    """
    from datetime import UTC, datetime as _dt

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
            config_snapshot={
                "wiki_filter": wiki_filter,
                "defrag": defrag,
                "dry_run": dry_run,
                "stale_threshold": stale_threshold,
            },
            dry_run=dry_run,
            run_id=rid,
        )

    def _emit(record_type: str, **fields: object) -> None:
        if logger is not None:
            logger.emit(record_type, **fields)

    # Use context manager if logger exists; otherwise a no-op.
    import contextlib
    ctx = logger if logger is not None else contextlib.nullcontext()

    with ctx:
        if defrag and lore_root is not None:
            wikis = _filter_already_ran_this_week(wikis, lore_root, now, _emit)

        for wiki_path in wikis:
            _emit("wiki-start", wiki=wiki_path.name)

            if is_obsidian_holding(wiki_path) and not dry_run:
                console.print(
                    f"[yellow]Warning:[/yellow] Obsidian appears active in "
                    f"{wiki_path}. Proceeding — but if you have mid-edit "
                    "buffers, close them first."
                )

            if defrag:
                from lore_curator.c_passes import has_merge_conflicts
                if has_merge_conflicts(wiki_path):
                    _emit("wiki-skip", wiki=wiki_path.name, reason="merge_conflicts")
                    console.print(
                        f"[yellow]Skipping wiki/{wiki_path.name}:[/yellow] "
                        "merge conflicts detected. Resolve first."
                    )
                    continue

            report = CuratorReport(wiki=wiki_path.name)
            report.actions.extend(_pass_staleness(wiki_path, today, stale_threshold))
            report.actions.extend(_pass_supersession(wiki_path))
            report.actions.extend(_pass_implements(wiki_path))
            report.actions.extend(_pass_git_backfill(wiki_path))
            if defrag:
                report.actions.extend(_pass_draft_promotion(wiki_path, today))
            report.hints.extend(_pass_team_mode_hint(wiki_path))
            reports.append(report)

        for report in reports:
            _print_report(report, dry_run)

        defrag_summary_by_wiki: dict[str, dict[str, int]] = {}
        wiki_snapshots_before: dict[str, dict] = {}

        if defrag:
            for wiki_path in wikis:
                wiki_snapshots_before[wiki_path.name] = _snapshot_wiki(wiki_path)

        if not dry_run:
            for report in reports:
                for action in report.actions:
                    ok, reason = _apply_safely(action)
                    if ok:
                        _emit("action-applied", wiki=report.wiki,
                              kind=action.kind, path=str(action.path), reason=action.reason)
                    else:
                        report.skipped.append((action.path, reason))
                        _emit("action-skipped", wiki=report.wiki,
                              path=str(action.path), reason=reason)
                        console.print(
                            f"  [red]skipped[/red] {action.path.name}: {reason}"
                        )

        if defrag:
            for wiki_path in wikis:
                summary = _run_defrag_passes(
                    wiki_path, llm_client=llm_client, dry_run=dry_run
                )
                defrag_summary_by_wiki[wiki_path.name] = summary
                _emit("defrag-pass", wiki=wiki_path.name, summary=summary)

        for report in reports:
            wiki_path = next(w for w in wikis if w.name == report.wiki)
            _write_review(wiki_path, report)

        if defrag and lore_root is not None:
            _write_defrag_diff_logs(
                wikis,
                lore_root=lore_root,
                run_id=rid,
                snapshots_before=wiki_snapshots_before,
                summary_by_wiki=defrag_summary_by_wiki,
                reports=reports,
                dry_run=dry_run,
                now=now,
            )
            _finalize_curator_c_ledger(wikis, lore_root, now)

    return reports


def _filter_already_ran_this_week(
    wikis: list[Path],
    lore_root: Path,
    now: "datetime",
    emit: "Callable[..., None]",
) -> list[Path]:
    """Return ``wikis`` minus any whose ``last_curator_c`` falls in the
    current ISO week — Curator C runs at most once per week per wiki."""
    iso_now = now.isocalendar()[:2]
    already_ran: set[str] = set()
    for wiki_path in wikis:
        entry = WikiLedger(lore_root, wiki_path.name).read()
        last_c = entry.last_curator_c
        if last_c is None:
            continue
        if last_c.tzinfo is None:
            from datetime import UTC as _UTC
            last_c = last_c.replace(tzinfo=_UTC)
        if last_c.isocalendar()[:2] == iso_now:
            already_ran.add(wiki_path.name)
            emit("wiki-skip", wiki=wiki_path.name, reason="already_ran_this_iso_week")
            console.print(
                f"[dim]Skipping wiki/{wiki_path.name}: already ran "
                f"this ISO week ({last_c.isoformat()}).[/dim]"
            )
    if already_ran:
        return [w for w in wikis if w.name not in already_ran]
    return wikis


def _write_defrag_diff_logs(
    wikis: list[Path],
    *,
    lore_root: Path,
    run_id: str,
    snapshots_before: dict[str, dict],
    summary_by_wiki: dict[str, dict[str, int]],
    reports: list[CuratorReport],
    dry_run: bool,
    now: "datetime",
) -> None:
    """Write per-wiki diff-log entries summarising what defrag changed,
    then prune logs past the 90-day retention window."""
    from lore_curator.curator_c_diff import (
        prune_old_diff_logs,
        write_diff_log_entry,
    )
    for wiki_path in wikis:
        before = snapshots_before.get(wiki_path.name, {})
        after = _snapshot_wiki(wiki_path)
        summary = summary_by_wiki.get(wiki_path.name, {})
        hygiene_report = next(
            (r for r in reports if r.wiki == wiki_path.name), None
        )
        if hygiene_report:
            for action in hygiene_report.actions:
                summary[action.kind] = summary.get(action.kind, 0) + 1
        write_diff_log_entry(
            lore_root,
            run_id=run_id,
            snapshot_before=before,
            snapshot_after=after,
            dry_run=dry_run,
            summary=summary,
            now=now,
        )
    prune_old_diff_logs(lore_root)


def _finalize_curator_c_ledger(
    wikis: list[Path], lore_root: Path, now: "datetime"
) -> None:
    """Update ``WikiLedger.last_curator_c`` for each successfully-processed
    wiki. Logged-but-swallowed on failure: a ledger write hiccup must not
    abort an otherwise-successful curator run."""
    for wiki_path in wikis:
        try:
            WikiLedger(lore_root, wiki_path.name).update_last_curator("c", at=now)
        except OSError:
            # Atomic-write failure (disk full / permission) — leave the
            # ledger untouched; next run will re-attempt.
            pass


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

from lore_runtime.argv import argv_main  # noqa: E402

app = typer.Typer(
    add_completion=False,
    help="Curator — flag stale notes, propagate `implements:` status flips, etc.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback(invoke_without_command=True)
def curator(
    ctx: typer.Context,
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
    # If a subcommand was invoked (e.g. `lore curator run --defrag`), let it
    # handle the flow; the callback's hygiene-only path is for bare
    # `lore curator` only.
    if ctx.invoked_subcommand is not None:
        return
    if migrate_open_items:
        run_open_items_migration(wiki_filter=wiki, dry_run=not apply)
        return

    reports = run_curator_c(
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


def _make_live_renderer(con: Console):
    """Return an on_record callback that prints the same format as ``lore runs tail``."""
    from typing import Any
    from lore_runtime.run_render import pick_icon_set, render_flat_log, should_use_color

    icons = pick_icon_set()
    use_color = should_use_color()

    def _render(_record_type: str, payload: dict[str, Any]) -> None:
        con.print(render_flat_log([payload], icons=icons, use_color=use_color))

    return _render


_BACKEND_LABELS = {
    "subprocess": "Claude Code subscription (claude -p)",
    "sdk": "Anthropic API (anthropic SDK)",
    "openai": "OpenAI-compatible endpoint",
}


def _resolve_backend(cli_backend: str | None, lore_root: Path) -> str | None:
    """Resolve curator backend from CLI flag → env var → root config → auto.

    The returned value is what ``make_llm_client(backend=...)`` expects:
    ``"subscription"`` | ``"api"`` | ``"openai"`` | ``"auto"`` | ``None``.
    """
    import os as _os
    if cli_backend:
        return cli_backend.strip()
    env = _os.environ.get("LORE_LLM_BACKEND", "").strip().lower()
    if env:
        return env
    try:
        from lore_core.root_config import load_root_config
        cfg_backend = load_root_config(lore_root).curator.backend.strip().lower()
        if cfg_backend and cfg_backend != "auto":
            return cfg_backend
    except Exception:
        pass
    return None


def _print_backend_label(con: Console, llm_client: object) -> None:
    backend_name = getattr(llm_client, "backend_name", "") or ""
    label = _BACKEND_LABELS.get(backend_name, backend_name or "unknown backend")
    con.print(f"[dim]Curator backend: {label}[/dim]")


@app.command("run")
def run_command(
    scope: str = typer.Option(None, "--scope", help="Filter to one scope, e.g. 'mywiki:subproject'."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Classify but don't write notes or advance ledger."),
    abstract: bool = typer.Option(False, "--abstract", help="Also run the surface-extraction pass after filing session notes."),
    defrag: bool = typer.Option(False, "--defrag", help="Run Curator C weekly defragmentation (hygiene + LLM adjacent-merge / auto-supersede / orphan-repair / draft-promotion)."),
    wiki: str = typer.Option(None, "--wiki", help="Limit the surface-extraction / defrag pass to a single wiki."),
    trace_llm: bool = typer.Option(False, "--trace-llm", help="Capture LLM prompts/responses to runs/<id>.trace.jsonl (equivalent to LORE_TRACE_LLM=1)."),
    backend: str = typer.Option(None, "--backend", help="LLM backend: subscription | api | openai | auto. Overrides LORE_LLM_BACKEND and curator.backend config."),
) -> None:
    """Run the curator.

    Default: classify pending transcripts and file session notes.
    --abstract also runs the surface-extraction pass.
    --defrag runs the weekly whole-wiki defragmentation (hygiene passes
    + LLM proposals for adjacent-concept merges, auto-supersession,
    orphan wikilink repair, and draft promotions).
    """
    import os as _os_check
    if defrag:
        # --defrag runs Curator C directly, not Curator A. Bypass the
        # transcript classification path and go straight to the whole-wiki
        # pipeline.
        from pathlib import Path as _P
        lore_root_str = _os_check.environ.get("LORE_ROOT", "")
        if not lore_root_str:
            console.print("[red]Error:[/red] LORE_ROOT environment variable not set.")
            raise typer.Exit(1)

        lore_root_defrag = _P(lore_root_str)
        effective_backend = _resolve_backend(backend, lore_root_defrag)

        # LLM client resolution (same seam as Curator A).
        from lore_curator.llm_client import LlmClientError, make_llm_client
        err_console = Console(stderr=True)
        api_key = _os_check.environ.get("ANTHROPIC_API_KEY", "") or None
        try:
            llm_client = make_llm_client(
                backend=effective_backend,
                api_key=api_key,
                lore_root=lore_root_defrag,
            )
        except LlmClientError as exc:
            err_console.print(f"[yellow]Warning:[/yellow] {exc}")
            llm_client = None
        if llm_client is None:
            console.print(
                "[yellow]Running --defrag without an LLM client — "
                "LLM passes (adjacent-merge, auto-supersede, orphan-repair) "
                "will be skipped.[/yellow]"
            )
        else:
            _print_backend_label(console, llm_client)

        reports = run_curator_c(
            wiki_filter=wiki,
            dry_run=dry_run,
            defrag=True,
            llm_client=llm_client,
        )
        # Exit success — report already printed by run_curator_c.
        return
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

    # Resolve LLM backend via factory
    from lore_curator.llm_client import LlmClientError, make_llm_client

    effective_backend = _resolve_backend(backend, lore_root)

    err_console = Console(stderr=True)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or None
    backend_error: LlmClientError | None = None
    try:
        llm_client = make_llm_client(
            backend=effective_backend,
            api_key=api_key,
            lore_root=lore_root,
        )
    except LlmClientError as exc:
        err_console.print(f"[yellow]Warning:[/yellow] {exc}")
        llm_client = None
        backend_error = exc

    if llm_client is None:
        if backend_error is None:
            # Auto-detection had nothing to pick — print the skip-AI warning.
            err_console.print(
                "[yellow]Warning:[/yellow] Curator will skip AI classification: "
                "neither 'claude' CLI on PATH nor ANTHROPIC_API_KEY set. Install "
                "Claude Code for subscription inference, or export "
                "ANTHROPIC_API_KEY for API inference."
            )
        # If backend_error was set, the specific warning was already printed.
    else:
        _print_backend_label(console, llm_client)

    # Interactive (TTY) runs wait up to 60s for the lock; detached hook spawns
    # keep timeout=0.0 (fire-and-forget, yield to any active curator).
    is_tty = sys.stdout.isatty()
    lock_timeout = 60.0 if is_tty else 0.0

    # Compute effective trace_llm from flag or environment variable
    effective_trace = trace_llm or os.environ.get("LORE_TRACE_LLM") == "1"

    live_callback = _make_live_renderer(console) if is_tty else None

    result = run_curator_a(
        lore_root=lore_root,
        scope=scope_obj,
        llm_client=llm_client,
        dry_run=dry_run,
        now=datetime.now(UTC),
        lock_timeout=lock_timeout,
        trigger="manual",
        trace_llm=effective_trace,
        on_record=live_callback,
    )

    skipped_summary = ", ".join(
        f"{k}: {v}" for k, v in result.skipped_reasons.items()
    ) or "none"

    console.print()
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
                llm_client=llm_client,
                dry_run=dry_run,
                now=datetime.now(UTC),
                lock_timeout=lock_timeout,
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
