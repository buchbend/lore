"""Run-log writer for Curator A invocations.

Two output files per run:
  - runs/<id>.jsonl            archival
  - runs-live.jsonl            tee of active run (truncated at run-start)

Plus an optional LLM-trace companion runs/<id>.trace.jsonl when
LORE_TRACE_LLM=1 or --trace-llm is set.
"""

from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime


_ID_ALPHABET = string.ascii_lowercase + string.digits  # 36 chars


def generate_run_id(*, now: datetime | None = None) -> str:
    """Return `<ISO-timestamp>-<6-char-random-suffix>` for a run.

    Timestamp is filename-safe (hyphens, no colons). Suffix is 6
    chars from [a-z0-9] — collisions inside the retention window
    are astronomically unlikely.
    """
    ts = now or datetime.now(UTC)
    stamp = ts.strftime("%Y-%m-%dT%H-%M-%S")
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"{stamp}-{suffix}"
