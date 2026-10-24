"""Token records: one per Claude Code session, kept over time.

The SessionEnd hook (``lore hook token-usage``) reads the finished
session's transcript with :func:`lore_core.token_trace.trace_tokens` and
appends one token record, a JSON line, to ``<lore_root>/.lore/token-usage.jsonl``.
A session that ends more than once (a resume) gets a new record each time;
the last record for a session id wins, because each one recounts the whole
transcript.

The file is machine-local like the transcript index: it sits beside
``wiki/``, never inside it, so no wiki push can ship it.

A record::

    {"v": 1, "session": "...", "recorded_at": "2026-10-24T09:00:00Z",
     "cwd": "/repo", "rows": [{"phase": "skill:build", "model": "claude-opus-5-5",
     "requested_model": null, "requested_effort": null, "messages": 3,
     "input": ..., "output": ..., "cache_read": ..., "cache_write": ...,
     "cache_write_1h": ...}]}

One row per (phase, served model). ``requested_*`` is set on agent rows
whose spawn named a model or effort.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lore_core.token_prices import estimate_cost
from lore_core.token_trace import model_matches, trace_tokens

RECORDS_NAME = "token-usage.jsonl"
SCHEMA_VERSION = 1
_COUNT_KEYS = ("messages", "input", "output", "cache_read", "cache_write", "cache_write_1h")


def records_path(lore_root: Path) -> Path:
    return Path(lore_root) / ".lore" / RECORDS_NAME


def build_record(
    session: str, transcript: Path, *, cwd: str = "", now: datetime | None = None
) -> dict:
    """The token record for the transcript at ``transcript``."""
    merged: dict[tuple, dict] = {}
    for phase in trace_tokens(transcript):
        for model, usage in phase.models.items():
            key = (phase.name, model, phase.requested_model, phase.requested_effort)
            row = merged.setdefault(
                key,
                {
                    "phase": phase.name,
                    "model": model,
                    "requested_model": phase.requested_model,
                    "requested_effort": phase.requested_effort,
                    **dict.fromkeys(_COUNT_KEYS, 0),
                },
            )
            counts = usage.counts()
            for k in _COUNT_KEYS:
                row[k] += counts[k]
    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "v": SCHEMA_VERSION,
        "session": session,
        "recorded_at": stamp,
        "cwd": cwd,
        "rows": list(merged.values()),
    }


def append_record(lore_root: Path, record: dict) -> Path:
    """Append ``record`` as one line. Locked: a long record exceeds the atomic-append size."""
    from lore_core.lockfile import flocked

    path = records_path(lore_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with flocked(path.with_suffix(".lock")), path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    return path


def record_session(lore_root: Path, session: str, transcript: Path, *, cwd: str = "") -> dict:
    record = build_record(session, transcript, cwd=cwd)
    append_record(lore_root, record)
    return record


def load_records(lore_root: Path) -> list[dict]:
    """The latest record per session, in file order. Unreadable lines are skipped."""
    try:
        text = records_path(lore_root).read_text(encoding="utf-8")
    except OSError:
        return []
    latest: dict[str, dict] = {}
    for raw in text.splitlines():
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("session"), str):
            latest.pop(record["session"], None)
            latest[record["session"]] = record
    return list(latest.values())


def _recorded_at(record: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(record.get("recorded_at")).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class Bucket:
    """Token counts and estimated cost for one group of record rows."""

    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_COUNT_KEYS, 0))
    cost: float = 0.0
    unpriced: int = 0  # rows whose model has no price

    @property
    def total(self) -> int:
        c = self.counts
        return c["input"] + c["output"] + c["cache_read"] + c["cache_write"]

    def add(self, row: dict, cost: float | None) -> None:
        for k in _COUNT_KEYS:
            value = row.get(k, 0)
            self.counts[k] += value if isinstance(value, int) else 0
        if cost is None:
            self.unpriced += 1
        else:
            self.cost += cost

    def as_dict(self) -> dict:
        return {**self.counts, "total": self.total, "cost_usd": round(self.cost, 4)}


@dataclass
class Mismatch:
    session: str
    phase: str
    requested: str
    served: str


@dataclass
class Summary:
    sessions: int = 0
    total: Bucket = field(default_factory=Bucket)
    by_model: dict[str, Bucket] = field(default_factory=dict)
    by_where: dict[str, Bucket] = field(default_factory=dict)
    by_request: dict[str, Bucket] = field(default_factory=dict)
    mismatches: list[Mismatch] = field(default_factory=list)
    unpriced_models: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "sessions": self.sessions,
            "total": self.total.as_dict(),
            "by_model": {k: v.as_dict() for k, v in self.by_model.items()},
            "by_where": {k: v.as_dict() for k, v in self.by_where.items()},
            "by_request": {k: v.as_dict() for k, v in self.by_request.items()},
            "mismatches": [vars(m) for m in self.mismatches],
            "unpriced_models": sorted(self.unpriced_models),
        }


def _where(phase: str) -> str:
    """Group a phase for the summary: ``main``, ``skill:<name>`` or ``agents``."""
    return "agents" if phase.startswith("agent:") else phase


def _request_label(row: dict) -> str:
    model = row.get("requested_model") or "no model named"
    effort = row.get("requested_effort")
    return f"{model} @ {effort}" if effort else model


def summarize(
    records: list[dict], *, since: datetime | None = None, prices: dict[str, Any] | None = None
) -> Summary:
    """Aggregate ``records`` recorded at or after ``since`` (all when None).

    ``prices`` is the person's ``tokens.prices`` override block.
    """
    out = Summary()
    for record in records:
        when = _recorded_at(record)
        if since is not None and (when is None or when < since):
            continue
        out.sessions += 1
        mismatched: set[str] = set()
        for row in record.get("rows") or []:
            if not isinstance(row, dict):
                continue
            model, phase = str(row.get("model") or "?"), str(row.get("phase") or "?")
            cost = estimate_cost(model, row, prices)
            if cost is None:
                out.unpriced_models.add(model)
            buckets = [out.total, out.by_model.setdefault(model, Bucket())]
            buckets.append(out.by_where.setdefault(_where(phase), Bucket()))
            if phase.startswith("agent:"):
                buckets.append(out.by_request.setdefault(_request_label(row), Bucket()))
            for bucket in buckets:
                bucket.add(row, cost)
            requested = row.get("requested_model")
            if (
                requested
                and model != "?"
                and not model_matches(requested, model)
                and phase not in mismatched
            ):
                mismatched.add(phase)
                out.mismatches.append(
                    Mismatch(str(record.get("session")), phase, str(requested), model)
                )
    return out
