"""Read-side of the run-log archives.

The file-based ``list_archival_runs`` / ``iter_archival_runs`` / ``read_run``
helpers serve the retention janitor and any on-disk archives.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path


CURRENT_SCHEMA_VERSION = 1


class SchemaVersionTooNew(ValueError):
    """Raised by read_run in strict mode when a record has schema_version > current."""
    def __init__(self, version: int):
        super().__init__(
            f"run written by newer lore (schema v{version}). Upgrade CLI to read."
        )
        self.version = version




def list_archival_runs(lore_root: Path) -> list[Path]:
    """Return archival run files sorted oldest → newest (chronological).

    Used by the retention janitor for FIFO deletion (run *resolution* now
    reads spine run_ids, not files). For newest-first iteration (the common case for
    renderers) use :func:`iter_archival_runs`.

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


# Back-compat alias; prefer list_archival_runs in new code.
_list_runs = list_archival_runs


def iter_archival_runs(
    lore_root: Path,
    *,
    limit: int | None = None,
) -> Iterator[Path]:
    """Yield archival run files, newest → oldest, filtering .trace.jsonl.

    Ordering is deterministic: run IDs are timestamp-prefixed, so lex
    order == chronological. Ties (same-second writes) are broken by the
    random suffix also being lex-sorted, so the order is stable.

    Partial-write / zero-byte files are still yielded (callers decide
    how to handle them); this helper only enumerates paths.
    """
    runs = list_archival_runs(lore_root)
    reversed_runs = reversed(runs)
    if limit is None:
        yield from reversed_runs
        return
    count = 0
    for path in reversed_runs:
        if count >= limit:
            return
        yield path
        count += 1


def read_run(path: Path, *, strict_schema: bool = True) -> list[dict]:
    """Return records from a run JSONL, tolerant of corruption.

    - Malformed JSON lines → {'type': '_malformed', 'raw': <line>}
    - Unparseable last line AND no 'run-end' → synthetic 'run-truncated'
    - If strict_schema=True and any record has schema_version > current,
      raise SchemaVersionTooNew.
    - If strict_schema=False, unknown-schema records get '_schema_mismatch': True.
    """
    raw_lines = path.read_text().splitlines(keepends=True)
    records: list[dict] = []
    last_line_broken = False
    max_schema_seen = 0
    for i, line in enumerate(raw_lines):
        stripped = line.rstrip("\n")
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            if i == len(raw_lines) - 1 and not stripped.endswith("}"):
                last_line_broken = True
            records.append({"type": "_malformed", "raw": stripped})
            continue
        sv = record.get("schema_version", 1)
        if isinstance(sv, int) and sv > CURRENT_SCHEMA_VERSION:
            max_schema_seen = max(max_schema_seen, sv)
            if not strict_schema:
                record["_schema_mismatch"] = True
        records.append(record)
    if strict_schema and max_schema_seen > CURRENT_SCHEMA_VERSION:
        raise SchemaVersionTooNew(max_schema_seen)
    saw_run_end = any(r.get("type") == "run-end" for r in records)
    if last_line_broken and not saw_run_end:
        if records and records[-1].get("type") == "_malformed":
            records.pop()
        records.append({
            "type": "run-truncated",
            "schema_version": 1,
            "note": "run appears to have been interrupted (last bytes unparseable)",
        })
    return records
