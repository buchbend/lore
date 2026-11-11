"""Read-side of the run-log archives, for the retention janitor."""

from __future__ import annotations

from pathlib import Path


def list_archival_runs(lore_root: Path) -> list[Path]:
    """Return archival run files sorted oldest → newest (chronological).

    Used by the retention janitor for FIFO deletion.

    Excludes ``.trace.jsonl`` companions. Returns empty list if the
    ``.lore/runs/`` directory doesn't exist.
    """
    runs_dir = lore_root / ".lore" / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        (p for p in runs_dir.glob("*.jsonl") if not p.name.endswith(".trace.jsonl")),
        key=lambda p: p.name,  # timestamp-prefixed → lexicographic == chronological
    )
