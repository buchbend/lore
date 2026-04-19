"""Best-effort secret redaction pre-pass for transcripts and notes."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
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


def redact(
    text: str,
    *,
    log_path: Path | None = None,
) -> tuple[str, list[RedactionHit]]:
    """Replace common secret patterns with [REDACTED:<kind>] markers.

    Returns (redacted_text, hits). Caller inspects `hits` for telemetry.
    If `log_path` is provided, appends one JSONL entry per hit:

        {"schema_version": 1, "ts": "...", "kind": "...", "preview": "..."}

    Patterns are intentionally conservative: false-positives only cost a
    redaction marker; false-negatives can leak secrets. When in doubt the
    pattern stays.
    """
    hits: list[RedactionHit] = []
    result = text

    # Pattern order (non-overlapping):
    # 1. sk-api-key: \bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b
    # 2. ghp-token: \bghp_[A-Za-z0-9]{36}\b
    # 3. aiza-key: \bAIza[0-9A-Za-z_-]{35}\b
    # 4. aws-access-key: \bAKIA[0-9A-Z]{16}\b
    # 5. pem-private-key: -----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----
    # 6. jwt: \b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b (total >60 chars)
    # 7. high-entropy-credential: high-entropy values in password/secret/token/api_key assignments

    # Process patterns in order. After each match, update result and adjust offset.

    # 1. SK-API-KEY
    pattern_sk = re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")
    for match in pattern_sk.finditer(text):
        original_start = match.start()
        original_end = match.end()
        secret = match.group()
        preview = secret[:6] + "…"
        hit = RedactionHit(
            kind="sk-api-key",
            start=original_start,
            end=original_end,
            preview=preview,
        )
        hits.append(hit)

    # 2. GHP-TOKEN
    pattern_ghp = re.compile(r"\bghp_[A-Za-z0-9]{36}\b")
    for match in pattern_ghp.finditer(text):
        original_start = match.start()
        original_end = match.end()
        secret = match.group()
        preview = secret[:6] + "…"
        hit = RedactionHit(
            kind="ghp-token",
            start=original_start,
            end=original_end,
            preview=preview,
        )
        hits.append(hit)

    # 3. AIZA-KEY
    pattern_aiza = re.compile(r"\bAIza[0-9A-Za-z_\-/+]{35}\b")
    for match in pattern_aiza.finditer(text):
        original_start = match.start()
        original_end = match.end()
        secret = match.group()
        preview = secret[:6] + "…"
        hit = RedactionHit(
            kind="aiza-key",
            start=original_start,
            end=original_end,
            preview=preview,
        )
        hits.append(hit)

    # 4. AWS-ACCESS-KEY
    pattern_aws = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
    for match in pattern_aws.finditer(text):
        original_start = match.start()
        original_end = match.end()
        secret = match.group()
        preview = secret[:6] + "…"
        hit = RedactionHit(
            kind="aws-access-key",
            start=original_start,
            end=original_end,
            preview=preview,
        )
        hits.append(hit)

    # 5. PEM-PRIVATE-KEY
    pattern_pem = re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"
    )
    for match in pattern_pem.finditer(text):
        original_start = match.start()
        original_end = match.end()
        secret = match.group()
        preview = secret[:6] + "…"
        hit = RedactionHit(
            kind="pem-private-key",
            start=original_start,
            end=original_end,
            preview=preview,
        )
        hits.append(hit)

    # 6. JWT (three base64url segments, total >60 chars)
    pattern_jwt = re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
    for match in pattern_jwt.finditer(text):
        secret = match.group()
        # Only include if total length > 60
        if len(secret) > 60:
            original_start = match.start()
            original_end = match.end()
            preview = secret[:6] + "…"
            hit = RedactionHit(
                kind="jwt",
                start=original_start,
                end=original_end,
                preview=preview,
            )
            hits.append(hit)

    # 7. HIGH-ENTROPY-CREDENTIAL
    # Pattern: (password|secret|token|api[_-]?key)\s*[:=]\s*["']?(?P<v>[A-Za-z0-9/+_=.-]{24,})["']?
    # Only redact the v group if Shannon entropy > 4.0
    pattern_hec = re.compile(
        r"(?i)\b(?:password|secret|token|api[_-]?key)\s*[:=]\s*[\"']?(?P<v>[A-Za-z0-9/+_=.-]{24,})[\"']?"
    )
    for match in pattern_hec.finditer(text):
        value = match.group("v")
        entropy = _shannon_entropy(value)
        if entropy > 4.0:
            # The hit should cover just the value, not the whole assignment
            value_start = match.start("v")
            value_end = match.end("v")
            preview = value[:6] + "…"
            hit = RedactionHit(
                kind="high-entropy-credential",
                start=value_start,
                end=value_end,
                preview=preview,
            )
            hits.append(hit)

    # Remove overlapping hits (preserve order; keep first match when overlap occurs)
    # Hits are added in pattern-order, so first-added wins on overlap
    deduplicated: list[RedactionHit] = []
    for hit in hits:
        # Check if this hit overlaps with any already-added hit
        overlaps = False
        for existing in deduplicated:
            # Overlap if: existing.start < hit.end and hit.start < existing.end
            if existing.start < hit.end and hit.start < existing.end:
                overlaps = True
                break
        if not overlaps:
            deduplicated.append(hit)
    hits = deduplicated

    # Now rebuild the result text with redactions, adjusting for replacements
    if hits:
        # Build result by replacing hits in reverse order (to preserve offsets)
        for hit in reversed(hits):
            marker = f"[REDACTED:{hit.kind}]"
            result = result[: hit.start] + marker + result[hit.end :]

    # Log hits if log_path provided
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
