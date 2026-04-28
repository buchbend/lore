"""Atomic per-step status mutation.

Every write to a plan note's ``step_status`` dict goes through here.
The contract:

* **Per-slug ``flock``** (same lock the writer takes) — no last-write-wins
  data loss on concurrent ``set_step`` calls.
* **Read-modify-write under lock**: re-parses the file *inside* the lock
  so a concurrent writer's update is visible.
* **Bumps ``step_status_updated``** to ISO-8601 UTC on every successful
  mutation — staleness derivation needs this to advance.
* **No-op on identical writes**: setting the same status twice doesn't
  rewrite the file (preserves mtime, avoids spurious git diffs).

This is the single authoritative writer of ``step_status``. Hook,
breadcrumbs, and SessionStart only READ the field; they never mutate.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

import yaml

from lore_core.io import atomic_write_text
from lore_core.schema import parse_frontmatter, strip_frontmatter

from .registry import extract_step_ids_from_body
from .types import StepStatus
from .writer import _slug_lock, plan_path, plans_dir


@dataclass(frozen=True)
class StepStatusUpdate:
    """Outcome of a step_status mutation."""

    slug: str
    step_id: str
    previous: str | None  # absent → pending; otherwise the prior status string
    current: str | None  # None → pending (entry removed); otherwise new status
    bumped_timestamp: str  # ISO-8601 UTC; same value also written to frontmatter


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def set_step(
    *,
    wiki_root: Path,
    slug: str,
    step_id: str,
    status: StepStatus | str | None,
    now: datetime | None = None,
) -> StepStatusUpdate:
    """Set ``step_status[step_id]`` to ``status`` atomically.

    ``status`` accepts:
      * a :class:`StepStatus` enum value, or its string equivalent
        (``"done"``, ``"in_progress"``, ``"blocked"``);
      * ``None`` to *clear* the entry (move the step back to pending).

    Validates the step ID against the plan's actual step headings;
    raises ``ValueError`` on unknown step IDs to catch typos at the
    point of action rather than silently storing garbage.
    """
    target_path = plan_path(wiki_root, slug)
    if not target_path.exists():
        raise FileNotFoundError(f"plan not found: {target_path}")

    pdir = plans_dir(wiki_root)
    pdir.mkdir(parents=True, exist_ok=True)

    # Normalize status input.
    if status is None:
        new_status_str: str | None = None
    elif isinstance(status, StepStatus):
        new_status_str = status.value
    else:
        new_status_str = StepStatus.from_str(str(status)).value

    with _slug_lock(pdir, slug):
        return _mutate_under_lock(
            target_path=target_path,
            slug=slug,
            step_id=step_id,
            new_status=new_status_str,
            now=now or datetime.now(UTC),
        )


def advance(
    *,
    wiki_root: Path,
    slug: str,
    now: datetime | None = None,
) -> StepStatusUpdate | None:
    """Sugar: mark the most-recent in-progress step done; else mark next pending done.

    Returns the resulting :class:`StepStatusUpdate`, or ``None`` if the
    plan has no remaining work (all steps already done, or zero steps).

    Resolution order:

    1. If any step is ``in_progress``, mark the *first* one done. This
       handles the parallel-agents case where multiple steps are
       in-progress — Claude/the user picks which one to advance via
       ``set_step`` directly when needed; ``advance`` always picks the
       earliest by document order for determinism.
    2. Otherwise mark the *first pending* step (no entry in step_status)
       done.
    3. If neither exists, return None.
    """
    target_path = plan_path(wiki_root, slug)
    if not target_path.exists():
        raise FileNotFoundError(f"plan not found: {target_path}")

    text = target_path.read_text()
    fm = parse_frontmatter(text)
    step_status = fm.get("step_status") or {}
    if not isinstance(step_status, dict):
        step_status = {}
    step_ids = extract_step_ids_from_body(text)

    # Pick the target step ID.
    target_step_id: str | None = None
    for sid in step_ids:
        if step_status.get(sid) == "in_progress":
            target_step_id = sid
            break
    if target_step_id is None:
        for sid in step_ids:
            if sid not in step_status:
                target_step_id = sid
                break
    if target_step_id is None:
        return None

    return set_step(
        wiki_root=wiki_root,
        slug=slug,
        step_id=target_step_id,
        status=StepStatus.DONE,
        now=now,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _mutate_under_lock(
    *,
    target_path: Path,
    slug: str,
    step_id: str,
    new_status: str | None,
    now: datetime,
) -> StepStatusUpdate:
    """Caller already holds the per-slug lock; do the read-modify-write."""
    text = target_path.read_text()
    fm = parse_frontmatter(text)
    body = strip_frontmatter(text)

    # Validate step_id against actual headings.
    step_ids = extract_step_ids_from_body(text)
    if step_id not in step_ids:
        raise ValueError(
            f"step {step_id!r} not in plan {slug!r}; valid steps: {step_ids}"
        )

    raw_step_status = fm.get("step_status") or {}
    if not isinstance(raw_step_status, dict):
        raw_step_status = {}
    step_status = {str(k): str(v) for k, v in raw_step_status.items()}

    previous = step_status.get(step_id)

    # No-op fast path: same status (including both None / absent → preserve mtime).
    if previous == new_status:
        return StepStatusUpdate(
            slug=slug,
            step_id=step_id,
            previous=previous,
            current=new_status,
            bumped_timestamp=fm.get("step_status_updated") or _iso(now),
        )

    if new_status is None:
        step_status.pop(step_id, None)
    else:
        step_status[step_id] = new_status

    timestamp = _iso(now)
    fm["step_status"] = step_status
    fm["step_status_updated"] = timestamp

    # Defensive: hand-edits may have dropped these system-owned fields.
    # Re-emitting without them would leave the linter unable to identify
    # the file as a plan note. Cheap insurance.
    fm.setdefault("schema_version", 2)
    fm.setdefault("type", "plan")

    new_text = _render_with_fm(fm, body)
    atomic_write_text(target_path, new_text)
    return StepStatusUpdate(
        slug=slug,
        step_id=step_id,
        previous=previous,
        current=new_status,
        bumped_timestamp=timestamp,
    )


def _iso(dt: datetime) -> str:
    """ISO-8601 with explicit ``Z`` suffix (no microseconds, for stable diffs)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_with_fm(fm: dict, body: str) -> str:
    dumped = yaml.safe_dump(
        fm, default_flow_style=False, sort_keys=False, allow_unicode=True
    ).strip()
    body = body.rstrip()
    return f"---\n{dumped}\n---\n\n{body}\n"
