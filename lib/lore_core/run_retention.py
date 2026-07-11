"""Legacy run-archival retention — one family the unified janitor (#190)
enforces (see lore_core.janitor).

RunLogger no longer writes ``.lore/runs/*.jsonl`` (curator runs live on the
event spine, see run_log.py), so this only cleans up pre-migration archives
that may still be on disk. Deletions and delete failures are logged onto
the spine (source="janitor") instead of the old silent-swallow — see
:func:`_safe_unlink`. Still best-effort: never raises.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.spine import SpineWriter


def _safe_unlink(path: Path, *, writer: SpineWriter, family: str) -> bool:
    """Delete ``path``, logging the outcome onto the spine. Return True iff
    deleted (or already gone). Never raises — a failure emits a warn event
    instead of being swallowed silently.
    """
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    try:
        path.unlink()
        writer.emit(
            source="janitor",
            event="retention-delete",
            data={"family": family, "path": path.name, "bytes": size},
        )
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        # Windows: open files raise PermissionError. POSIX: perms.
        writer.emit(
            source="janitor",
            event="retention-delete-failed",
            level="warn",
            data={"family": family, "path": path.name, "error": str(exc)},
        )
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

    writer = SpineWriter(lore_root)

    try:
        from lore_core.run_reader import list_archival_runs
        archival = list_archival_runs(lore_root)
        trace = sorted(runs.glob("*.trace.jsonl"), key=lambda p: p.name)
    except OSError:
        return

    def _unlink(path: Path) -> bool:
        return _safe_unlink(path, writer=writer, family="run-archival")

    # 1) Count cap on archival (delete oldest first).
    while len(archival) > keep:
        victim = archival[0]
        if _unlink(victim):
            archival.pop(0)
            trace_sibling = runs / (victim.stem + ".trace.jsonl")
            if trace_sibling.exists():
                _unlink(trace_sibling)
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
        if _unlink(victim):
            archival.pop(0)
            trace_sibling = runs / (victim.stem + ".trace.jsonl")
            if trace_sibling.exists():
                _unlink(trace_sibling)
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
            _unlink(t)
        else:
            trace_live.append(t)

    # 4) Trace cap.
    while len(trace_live) > keep_trace:
        victim = trace_live[0]
        if _unlink(victim):
            trace_live.pop(0)
        else:
            break
