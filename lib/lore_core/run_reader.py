"""Read-side of the run-log subsystem.

- resolve_run_id():    user identifier → archival file Path
- read_run():          JSONL → list[dict] with tolerant parsing
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

    Replaces five pre-Task-8 copies of the same glob-sort-filter pattern
    (doctor, runs list, runs list --hooks, breadcrumb, run_retention).

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


def resolve_run_id(lore_root: Path, identifier: str) -> Path:
    """Resolve a user identifier to a run file path.

    Accepts:
      - 'latest'         → most recent run
      - '^1', '^2', …    → N-th most recent (^1 == latest)
      - full ID          → exact file
      - 6-char suffix    → unique short ID (e.g. 'a1b2c3')
      - any prefix       → if unique
    """
    runs = _list_runs(lore_root)
    if not runs:
        raise RunIdNotFound("no runs on disk")
    if identifier == "latest":
        return runs[-1]
    m = _CARET_RE.match(identifier)
    if m:
        n = int(m.group(1))
        if n < 1 or n > len(runs):
            raise RunIdNotFound(f"^{n} out of range (have {len(runs)} runs)")
        return runs[-n]
    if re.fullmatch(r"[a-z0-9]{6}", identifier):
        matches = [p for p in runs if p.stem.endswith(f"-{identifier}")]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RunIdAmbiguous([p.stem for p in matches])
    matches = [p for p in runs if p.stem.startswith(identifier)]
    if not matches:
        raise RunIdNotFound(identifier)
    if len(matches) > 1:
        raise RunIdAmbiguous([p.stem for p in matches])
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
