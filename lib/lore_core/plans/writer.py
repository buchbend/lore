"""Plan note writer: ``StructuredPlan`` + frontmatter → ``wiki/plans/<slug>.md``.

Contracts pinned in the implementation plan:

* **Frontmatter rendering uses ``yaml.safe_dump``**, not
  ``lore_core.session.format_frontmatter``. Plan descriptions are
  extracted from untrusted text and routinely contain colons that
  ``format_frontmatter``'s small char set doesn't quote.
* **Per-slug ``flock``** in ``wiki/<wiki>/plans/.<slug>.lock`` (5 s
  timeout). Only contends on same-slug races; cross-slug writes
  proceed in parallel. Required to close the check-then-write TOCTOU
  on slug-collision detection.
* **Source-hash dedup uses ``canonical_text``** so editor round trips
  (trailing newlines, CRLF) don't trigger spurious "different content"
  detection.
* **Idempotent re-capture preserves the user-owned whitelist**:
  ``{status, tags, spec, roadmap, notes, description, step_status,
  step_status_updated}``. Everything else (incl. ``last_reviewed``) is
  system-owned and refreshes on re-capture.
* **Step renumbering is forbidden**: re-capture with a different number
  of steps appends new ones as ``s<N+1>..s<N+M>`` and tags removed
  steps with ``[removed-from-source]`` rather than silently shifting
  IDs out from under existing ``step_status`` entries.
* **First-plan-in-fresh-wiki**: ``path.parent.mkdir(parents=True,
  exist_ok=True)`` runs *before* the lockfile open so an empty
  ``plans/`` directory doesn't ENOENT the very first capture.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Any, Iterator

import yaml

from lore_core.io import atomic_write_text, canonical_text
from lore_core.schema import parse_frontmatter, strip_frontmatter

from .types import StructuredPlan

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteResult:
    """Outcome of one write_plan_note call.

    ``outcome`` is one of:
      * ``"filed"`` — fresh plan written to a previously-absent path.
      * ``"deduped"`` — same content as before; no-op (idempotent re-capture).
      * ``"updated"`` — different content, status was active, refreshed in place.
      * ``"collision-suffixed"`` — different content, non-active status, written
        to a date-suffixed slug (``<slug>-YYYY-MM-DD``).
    """

    path: Path
    slug: str
    outcome: str
    step_count: int


# ---------------------------------------------------------------------------
# User-owned vs system-owned frontmatter
# ---------------------------------------------------------------------------

#: User-owned keys are PRESERVED on re-capture. Everything else (including
#: keys we don't know about today but might add later) is system-owned and
#: refreshes. Whitelisting the user side is the safer flip — new system
#: fields are correct by default; new user fields require a deliberate
#: addition to this set.
USER_OWNED_KEYS: frozenset[str] = frozenset({
    "status",
    "tags",
    "spec",
    "roadmap",
    "notes",
    "description",
    "step_status",
    "step_status_updated",
})

LOCK_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plans_dir(wiki_root: Path) -> Path:
    """Return ``<wiki_root>/plans``. Does NOT create the directory."""
    return wiki_root / "plans"


def plan_path(wiki_root: Path, slug: str) -> Path:
    return plans_dir(wiki_root) / f"{slug}.md"


def compute_source_hash(plan_text: str | bytes) -> str:
    """SHA-256 over canonicalized plan text. ``sha256:`` prefix included."""
    digest = hashlib.sha256(canonical_text(plan_text).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def write_plan_note(
    *,
    wiki_root: Path,
    plan: StructuredPlan,
    source_hash: str,
    source_adapter: str,
    repo: str | None = None,
    today: _date | None = None,
    description: str | None = None,
    extra_tags: list[str] | None = None,
) -> WriteResult:
    """Materialize a :class:`StructuredPlan` to ``wiki/plans/<slug>.md``.

    The slug-level flock serializes concurrent invocations on the same
    plan; cross-plan writes proceed in parallel.

    ``description`` defaults to the plan title when not supplied.
    """
    today = today or _date.today()
    plans_dir_path = plans_dir(wiki_root)
    # Critical: create the dir BEFORE the flock open so an empty
    # ``plans/`` doesn't ENOENT the first-plan-in-fresh-wiki case.
    plans_dir_path.mkdir(parents=True, exist_ok=True)

    target_slug = plan.slug
    target_path = plan_path(wiki_root, target_slug)

    with _slug_lock(plans_dir_path, target_slug):
        return _write_under_lock(
            wiki_root=wiki_root,
            target_path=target_path,
            plan=plan,
            source_hash=source_hash,
            source_adapter=source_adapter,
            repo=repo,
            today=today,
            description=description,
            extra_tags=extra_tags,
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@contextmanager
def _slug_lock(plans_dir_path: Path, slug: str) -> Iterator[None]:
    """Per-slug flock with a soft timeout.

    The lock file lives at ``<plans_dir>/.<slug>.lock`` so it sits next
    to the note but starts with a dot to keep glob('plans/*.md') clean.
    Lock files are 0-byte sentinels; we never write into them.
    """
    lock_path = plans_dir_path / f".{slug}.lock"
    # Make sure parent exists (defensive — write_plan_note already mkdir'd).
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"plan write lock {lock_path} not acquired within "
                        f"{LOCK_TIMEOUT_SECONDS}s"
                    ) from e
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _write_under_lock(
    *,
    wiki_root: Path,
    target_path: Path,
    plan: StructuredPlan,
    source_hash: str,
    source_adapter: str,
    repo: str | None,
    today: _date,
    description: str | None,
    extra_tags: list[str] | None,
) -> WriteResult:
    """Caller already holds the per-slug lock; do the read-check-write."""
    if not target_path.exists():
        return _file_fresh(
            target_path=target_path,
            plan=plan,
            source_hash=source_hash,
            source_adapter=source_adapter,
            repo=repo,
            today=today,
            description=description,
            extra_tags=extra_tags,
        )

    existing_text = target_path.read_text()
    existing_fm = parse_frontmatter(existing_text)
    existing_hash = existing_fm.get("source_hash")

    if existing_hash == source_hash:
        # No-op: idempotent re-capture.
        return WriteResult(
            path=target_path,
            slug=plan.slug,
            outcome="deduped",
            step_count=len(plan.steps),
        )

    existing_status = existing_fm.get("status", "active")
    if existing_status != "active":
        # Non-active status: don't overwrite; collision-suffix the new file.
        suffix_path = _date_suffixed_path(wiki_root, plan.slug, today)
        return _file_fresh(
            target_path=suffix_path,
            plan=plan,
            source_hash=source_hash,
            source_adapter=source_adapter,
            repo=repo,
            today=today,
            description=description,
            extra_tags=extra_tags,
            outcome="collision-suffixed",
        )

    # Active status + different hash: refresh in place, preserving user-owned
    # frontmatter and the existing step_status entries (renumber-safe).
    return _refresh_in_place(
        target_path=target_path,
        existing_fm=existing_fm,
        existing_text=existing_text,
        plan=plan,
        source_hash=source_hash,
        source_adapter=source_adapter,
        repo=repo,
        today=today,
        description=description,
        extra_tags=extra_tags,
    )


def _file_fresh(
    *,
    target_path: Path,
    plan: StructuredPlan,
    source_hash: str,
    source_adapter: str,
    repo: str | None,
    today: _date,
    description: str | None,
    extra_tags: list[str] | None,
    outcome: str = "filed",
) -> WriteResult:
    fm = _build_fresh_frontmatter(
        plan=plan,
        source_hash=source_hash,
        source_adapter=source_adapter,
        repo=repo,
        today=today,
        description=description,
        extra_tags=extra_tags,
        target_slug=target_path.stem,
    )
    body = _render_plan_body(plan)
    text = _render_markdown(fm, body)
    atomic_write_text(target_path, text)
    return WriteResult(
        path=target_path,
        slug=target_path.stem,
        outcome=outcome,
        step_count=len(plan.steps),
    )


def _refresh_in_place(
    *,
    target_path: Path,
    existing_fm: dict[str, Any],
    existing_text: str,
    plan: StructuredPlan,
    source_hash: str,
    source_adapter: str,
    repo: str | None,
    today: _date,
    description: str | None,
    extra_tags: list[str] | None,
) -> WriteResult:
    # Build the system-owned slice from scratch; merge user-owned keys
    # from the existing frontmatter.
    new_fm = _build_fresh_frontmatter(
        plan=plan,
        source_hash=source_hash,
        source_adapter=source_adapter,
        repo=repo,
        today=today,
        description=description,
        extra_tags=extra_tags,
        target_slug=target_path.stem,
    )
    for key in USER_OWNED_KEYS:
        if key in existing_fm:
            new_fm[key] = existing_fm[key]

    # Renumber-safe step merge: existing steps keep their IDs even when the
    # new source has additions / removals. New steps are appended; missing
    # steps are tagged ``[removed-from-source]`` so the user sees the drift.
    merged_steps = _merge_steps_renumber_safe(existing_text, plan)

    body = _render_plan_body_from_steps(plan, merged_steps)
    text = _render_markdown(new_fm, body)
    atomic_write_text(target_path, text)
    return WriteResult(
        path=target_path,
        slug=target_path.stem,
        outcome="updated",
        step_count=len(merged_steps),
    )


def _build_fresh_frontmatter(
    *,
    plan: StructuredPlan,
    source_hash: str,
    source_adapter: str,
    repo: str | None,
    today: _date,
    description: str | None,
    extra_tags: list[str] | None,
    target_slug: str,
) -> dict[str, Any]:
    today_iso = today.isoformat()
    fm: dict[str, Any] = {
        "schema_version": 2,
        "type": "plan",
        "slug": target_slug,
        "status": "active",
        "created": today_iso,
        "last_reviewed": today_iso,
        "description": description or plan.title or target_slug.replace("-", " "),
        "source_adapter": source_adapter,
        "source_hash": source_hash,
    }
    if repo:
        fm["repo"] = repo
    if extra_tags:
        fm["tags"] = list(extra_tags)
    return fm


def _render_plan_body(plan: StructuredPlan) -> str:
    """Render the body for a fresh plan: title H1 + intro + steps."""
    return _render_plan_body_from_steps(plan, _steps_with_status(plan.steps))


def _render_plan_body_from_steps(
    plan: StructuredPlan, steps: list[tuple[Any, str | None]]
) -> str:
    """Render with explicit step list (used by re-capture's renumber-safe merge).

    Each entry is ``(PlanStep, removed_marker_or_None)`` — ``removed_marker``
    is ``"[removed-from-source]"`` for steps that no longer appear in the
    source.

    Single-mode plans (no recognizable step boundaries) emit the source
    body verbatim — wrapping it under ``## Steps / ### s1: s1`` would
    nest the source's own H2 sections under H3 and mangle the visual
    hierarchy. The implicit ``s1`` anchor is still valid for step_status
    / breadcrumbs; it just isn't rendered as a heading.
    """
    lines: list[str] = []
    if plan.title:
        lines.append(f"# {plan.title}")
        lines.append("")
    if plan.body_intro:
        lines.append(plan.body_intro)
        lines.append("")

    if plan.mode == "single":
        if steps:
            single_body = steps[0][0].body.strip()
            if single_body:
                lines.append(single_body)
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    lines.append(
        "> Commit refs: `Plan: " + plan.slug + "#s<N>` (trailer-style; "
        "surfaced by SessionStart breadcrumbs)"
    )
    lines.append("")
    if steps:
        lines.append("## Steps")
        lines.append("")
        for step, removed_marker in steps:
            heading_title = step.title or step.id
            if removed_marker:
                lines.append(f"### {step.id}: {heading_title} {removed_marker}")
            else:
                lines.append(f"### {step.id}: {heading_title}")
            if step.body.strip():
                lines.append(step.body.strip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _steps_with_status(steps: list) -> list[tuple[Any, str | None]]:
    return [(s, None) for s in steps]


def _merge_steps_renumber_safe(
    existing_text: str, new_plan: StructuredPlan
) -> list[tuple[Any, str | None]]:
    """Preserve existing step IDs across re-capture.

    Strategy: pair existing steps with new steps by ordinal position
    (s1 ↔ first new, s2 ↔ second new, …). New steps beyond the existing
    count get fresh IDs ``s<N+1>..s<N+M>``. Existing steps with no
    matching new step are kept and tagged ``[removed-from-source]``.
    The ID space therefore monotonically grows; existing
    ``step_status`` entries never become orphaned.

    "Existing steps" are recovered by parsing ``### s<N>:`` headings in
    the previous file body. We don't try to be clever about reordering
    or content matching — ID stability is the load-bearing property.
    """
    from .parser import _STEP_HEADING_PATTERNS  # local import: same package

    existing_body = strip_frontmatter(existing_text)
    existing_step_ids: list[str] = []
    s_re = _STEP_HEADING_PATTERNS[1]  # the ### s<N> form
    for line in existing_body.split("\n"):
        m = s_re.match(line)
        if m:
            existing_step_ids.append(f"s{m.group(1)}")

    out: list[tuple[Any, str | None]] = []
    new_steps = list(new_plan.steps)

    # Phase A: for each existing s<N>, pair with the corresponding ordinal
    # new step (if any).
    for idx, eid in enumerate(existing_step_ids):
        if idx < len(new_steps):
            ns = new_steps[idx]
            # Replace the new step's id with the preserved existing id.
            from dataclasses import replace as _replace
            preserved = _replace(ns, id=eid)
            out.append((preserved, None))
        else:
            # Existing step has no counterpart — preserve with removed marker.
            # Body is empty because we don't carry it across; the user can
            # consult git history.
            from .types import PlanStep
            out.append(
                (PlanStep(id=eid, title="(removed)", body=""), "[removed-from-source]")
            )

    # Phase B: any new steps beyond existing count get fresh IDs.
    next_id = _next_id_after(existing_step_ids)
    for ns in new_steps[len(existing_step_ids):]:
        from dataclasses import replace as _replace
        new_id = f"s{next_id}"
        out.append((_replace(ns, id=new_id), None))
        next_id += 1

    return out


def _next_id_after(existing_ids: list[str]) -> int:
    """Return the next free numeric step suffix."""
    if not existing_ids:
        return 1
    max_n = 0
    for sid in existing_ids:
        if sid.startswith("s") and sid[1:].isdigit():
            max_n = max(max_n, int(sid[1:]))
    return max_n + 1


def _render_markdown(fm: dict[str, Any], body: str) -> str:
    """Render frontmatter + body into a final markdown document.

    Uses ``yaml.safe_dump`` (NOT ``format_frontmatter``) because plan
    descriptions may contain colons, brackets, and other YAML-fragile
    characters that the session-note formatter's small char set
    doesn't quote.
    """
    dumped = yaml.safe_dump(
        fm, default_flow_style=False, sort_keys=False, allow_unicode=True
    ).strip()
    body = body.rstrip()
    return f"---\n{dumped}\n---\n\n{body}\n"


def _date_suffixed_path(wiki_root: Path, slug: str, today: _date) -> Path:
    """Return ``plans/<slug>-<YYYY-MM-DD>.md``, with a counter on same-day collision."""
    base = plans_dir(wiki_root) / f"{slug}-{today.isoformat()}.md"
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = plans_dir(wiki_root) / f"{slug}-{today.isoformat()}-{counter}.md"
        if not candidate.exists():
            return candidate
        counter += 1
