"""Best-effort secret redaction pre-pass for transcripts and notes."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


REDACTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RedactionHit:
    """Record of a detected secret."""

    kind: str  # "sk-api-key" | "ghp-token" | "aiza-key" | "aws-access-key" |
    # "jwt" | "pem-private-key" | "high-entropy-credential"
    start: int  # offset in original text
    end: int
    preview: str  # first 6 chars + "…" for the log; never full secret


def _shannon_entropy(s: str) -> float:
    """Compute Shannon entropy (bits per character) of a string."""
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


@dataclass(frozen=True)
class _PatternSpec:
    """One secret-detection rule. ``group`` picks which regex group the
    hit covers (0 = whole match). ``predicate`` is an optional gate on
    the captured value — return True to keep the hit."""
    kind: str
    pattern: re.Pattern
    group: int | str = 0
    predicate: Callable[[str], bool] | None = None


# Patterns are intentionally conservative: false-positives only cost a
# redaction marker; false-negatives can leak secrets. Order matters for
# overlap dedup below — earlier matches win.
_PATTERNS: tuple[_PatternSpec, ...] = (
    _PatternSpec(
        kind="sk-api-key",
        pattern=re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b"),
    ),
    _PatternSpec(
        kind="ghp-token",
        pattern=re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    ),
    _PatternSpec(
        kind="aiza-key",
        pattern=re.compile(r"\bAIza[0-9A-Za-z_\-/+]{35}\b"),
    ),
    _PatternSpec(
        kind="aws-access-key",
        pattern=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    _PatternSpec(
        kind="pem-private-key",
        pattern=re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
        ),
    ),
    _PatternSpec(
        kind="jwt",
        pattern=re.compile(
            r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        predicate=lambda v: len(v) > 60,
    ),
    _PatternSpec(
        kind="high-entropy-credential",
        pattern=re.compile(
            r"(?i)\b(?:password|secret|token|api[_-]?key)\s*[:=]\s*"
            r"[\"']?(?P<v>[A-Za-z0-9/+_=.-]{24,})[\"']?"
        ),
        group="v",
        predicate=lambda v: _shannon_entropy(v) > 4.0,
    ),
)


def _collect_hits(text: str) -> list[RedactionHit]:
    """Scan ``text`` with every pattern; return hits in pattern order."""
    hits: list[RedactionHit] = []
    for spec in _PATTERNS:
        for match in spec.pattern.finditer(text):
            value = match.group(spec.group)
            if spec.predicate is not None and not spec.predicate(value):
                continue
            hits.append(RedactionHit(
                kind=spec.kind,
                start=match.start(spec.group),
                end=match.end(spec.group),
                preview=value[:6] + "…",
            ))
    return hits


def _drop_overlaps(hits: list[RedactionHit]) -> list[RedactionHit]:
    """First-added wins on overlap; preserves input order."""
    kept: list[RedactionHit] = []
    for hit in hits:
        if any(e.start < hit.end and hit.start < e.end for e in kept):
            continue
        kept.append(hit)
    return kept


def redact(
    text: str,
    *,
    log_path: Path | None = None,
) -> tuple[str, list[RedactionHit]]:
    """Replace common secret patterns with [REDACTED:<kind>] markers.

    Returns (redacted_text, hits). Caller inspects `hits` for telemetry.
    If `log_path` is provided, appends one JSONL entry per hit:

        {"schema_version": 1, "ts": "...", "kind": "...", "preview": "..."}
    """
    hits = _drop_overlaps(_collect_hits(text))

    result = text
    for hit in reversed(hits):
        result = result[: hit.start] + f"[REDACTED:{hit.kind}]" + result[hit.end :]

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            for hit in hits:
                entry = {
                    "schema_version": REDACTION_SCHEMA_VERSION,
                    "ts": datetime.now(UTC).isoformat(),
                    "kind": hit.kind,
                    "preview": hit.preview,
                }
                f.write(json.dumps(entry) + "\n")

    return result, hits
