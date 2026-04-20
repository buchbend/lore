"""Lazy retention cleanup for run logs.

Invoked at the end of each Curator run (from RunLogger.__exit__).
Best-effort — never raises; skips files that fail to unlink.
"""

from __future__ import annotations

from pathlib import Path


def _safe_unlink(path: Path) -> bool:
    """Return True iff path was deleted (or already gone)."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        # Windows: open files raise PermissionError. POSIX: perms.
        return False


def enforce_retention(
    lore_root: Path,
    *,
    keep: int,
    max_total_mb: int,
    keep_trace: int,
) -> None:
    """Enforce retention caps on $LORE_ROOT/.lore/runs/. Never raises."""
    runs = lore_root / ".lore" / "runs"
    if not runs.exists():
        return

    try:
        archival = sorted(
            (p for p in runs.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
            key=lambda p: p.name,
        )
        trace = sorted(runs.glob("*.trace.jsonl"), key=lambda p: p.name)
    except OSError:
        return

    # 1) Count cap on archival (delete oldest first).
    while len(archival) > keep:
        victim = archival[0]
        if _safe_unlink(victim):
            archival.pop(0)
            trace_sibling = runs / (victim.stem + ".trace.jsonl")
            if trace_sibling.exists():
                _safe_unlink(trace_sibling)
                if trace_sibling in trace:
                    trace.remove(trace_sibling)
        else:
            break

    # 2) MB cap on archival.
    max_bytes = max_total_mb * 1024 * 1024

    def _total() -> int:
        total = 0
        for p in archival:
            try:
                total += p.stat().st_size
            except OSError:
                continue
        return total

    while archival and _total() > max_bytes:
        victim = archival[0]
        if _safe_unlink(victim):
            archival.pop(0)
            trace_sibling = runs / (victim.stem + ".trace.jsonl")
            if trace_sibling.exists():
                _safe_unlink(trace_sibling)
                if trace_sibling in trace:
                    trace.remove(trace_sibling)
        else:
            break

    # 3) Orphan trace cleanup (.trace.jsonl without sibling .jsonl).
    archival_stems = {p.stem for p in archival}
    trace_live: list[Path] = []
    for t in trace:
        stem = t.name[: -len(".trace.jsonl")]
        if stem not in archival_stems:
            _safe_unlink(t)
        else:
            trace_live.append(t)

    # 4) Trace cap.
    while len(trace_live) > keep_trace:
        victim = trace_live[0]
        if _safe_unlink(victim):
            trace_live.pop(0)
        else:
            break
