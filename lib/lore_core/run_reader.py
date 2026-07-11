"""Read-side of the run-log subsystem.

Curator runs now live on the event spine (``source="curator"``), grouped
by ``run_id``; ``read_curator_runs`` / ``run_ids`` / ``read_run_by_id``
reconstruct a run's legacy-shaped record list from it. ``resolve_run_id``
maps a user identifier to a ``run_id`` (not a file path). The file-based
``list_archival_runs`` / ``iter_archival_runs`` / ``read_run`` helpers
remain for the retention janitor and any on-disk archives.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path


CURRENT_SCHEMA_VERSION = 1


class RunIdNotFound(ValueError):
    pass


class RunIdAmbiguous(ValueError):
    def __init__(self, matches: list[str]):
        super().__init__(f"ambiguous run ID, matches: {matches!r}")
        self.matches = matches


class SchemaVersionTooNew(ValueError):
    """Raised by read_run in strict mode when a record has schema_version > current."""
    def __init__(self, version: int):
        super().__init__(
            f"run written by newer lore (schema v{version}). Upgrade CLI to read."
        )
        self.version = version


_CARET_RE = re.compile(r"^\^(\d+)$")


def list_archival_runs(lore_root: Path) -> list[Path]:
    """Return archival run files sorted oldest → newest (chronological).

    Used by ``resolve_run_id`` for prefix matching and by retention for
    FIFO deletion. For newest-first iteration (the common case for
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


def read_curator_runs(lore_root: Path) -> dict[str, list[dict]]:
    """Group curator spine events by ``run_id`` into legacy-shaped records.

    Each spine envelope becomes a ``{type, ts, schema_version, **data}`` record
    (the shape the run renderers already consume); records stay in spine append
    order, i.e. chronological within a run. Envelopes without a ``run_id`` (the
    flush-state-machine and spawn events) are not part of any run.
    """
    from lore_core.spine import read_spine

    grouped: dict[str, list[dict]] = {}
    for rec in read_spine(lore_root, source="curator"):
        rid = rec.get("run_id")
        if not rid:
            continue
        legacy = dict(rec.get("data") or {})
        legacy["type"] = rec.get("event")
        legacy["ts"] = rec.get("ts")
        legacy.setdefault("schema_version", 1)
        grouped.setdefault(rid, []).append(legacy)
    return grouped


def run_ids(
    lore_root: Path, *, newest_first: bool = True, limit: int | None = None
) -> list[str]:
    """Return run_ids seen on the spine. run_id is ts-prefixed, so lexicographic
    order is chronological."""
    ids = sorted(read_curator_runs(lore_root).keys())
    if newest_first:
        ids = list(reversed(ids))
    if limit is not None:
        ids = ids[:limit]
    return ids


def read_run_by_id(lore_root: Path, run_id: str) -> list[dict]:
    """Return one run's reconstructed records, or [] if unknown."""
    return read_curator_runs(lore_root).get(run_id, [])


def resolve_run_id(lore_root: Path, identifier: str) -> str:
    """Resolve a user identifier to a ``run_id`` from the spine.

    Accepts:
      - 'latest'         → most recent run
      - '^1', '^2', …    → N-th most recent (^1 == latest)
      - full ID          → exact
      - 6-char suffix    → unique short ID (e.g. 'a1b2c3')
      - any prefix       → if unique
    """
    ids = run_ids(lore_root, newest_first=False)  # chronological
    if not ids:
        raise RunIdNotFound("no runs on the spine")
    if identifier == "latest":
        return ids[-1]
    m = _CARET_RE.match(identifier)
    if m:
        n = int(m.group(1))
        if n < 1 or n > len(ids):
            raise RunIdNotFound(f"^{n} out of range (have {len(ids)} runs)")
        return ids[-n]
    if re.fullmatch(r"[a-z0-9]{6}", identifier):
        matches = [rid for rid in ids if rid.endswith(f"-{identifier}")]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RunIdAmbiguous(matches)
    matches = [rid for rid in ids if rid.startswith(identifier)]
    if not matches:
        raise RunIdNotFound(identifier)
    if len(matches) > 1:
        raise RunIdAmbiguous(matches)
    return matches[0]


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
