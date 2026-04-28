"""Atomic I/O helpers — never leave a partial file behind.

Readers (SessionStart, PreCompact hooks) race with writers (linter,
curator, skill output). `.tmp + os.replace` ensures readers never see
a half-written file. POSIX atomic on same filesystem.

This module also hosts two small text helpers that turned out to need
exactly one home each across plan-capture and stdin-reading hook code
paths: ``canonical_text`` (deterministic normalization for hashing) and
``read_hook_stdin`` (bytes-mode stdin reader with size + TTY guards).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_HOOK_STDIN_MAX_BYTES = 1_048_576  # 1 MiB; ExitPlanMode plans top out ~50 KiB


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text to `path` atomically via a sibling .tmp file.

    If content ends without a newline, one is appended.

    Fsyncs the tmp file before rename so concurrent readers on the same
    host see a committed state even if we crash. Required by Plan 5's
    Curator C team-mode coordination (code-reviewer must-fix).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if not content.endswith("\n"):
        content += "\n"
    # Write + fsync the bytes, then atomic-rename.
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, content.encode(encoding))
        try:
            os.fsync(fd)
        except OSError:
            pass  # fsync not supported on some filesystems; rename still atomic
    finally:
        os.close(fd)
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to `path` atomically via a sibling .tmp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def canonical_text(text: str | bytes) -> str:
    """Deterministic text normalization for hashing/comparison.

    The plan-capture hook stores ``source_hash = sha256(canonical_text(plan))``
    and re-compares on every re-acceptance. Editor round-trips routinely
    add a trailing newline or convert line endings, so naive byte-equality
    triggers spurious "different content" detection.

    Pipeline:
      1. Decode bytes → str (UTF-8, ``errors='replace'``).
      2. Normalize ``\\r\\n`` and lone ``\\r`` to ``\\n``.
      3. Strip trailing whitespace per line.
      4. Collapse trailing newlines to a single ``\\n``.

    Idempotent: ``canonical_text(canonical_text(x)) == canonical_text(x)``.
    """
    s = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else text
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = s.rstrip("\n") + "\n"
    return s


class HookStdinResult:
    """Tagged result returned by ``read_hook_stdin``.

    The hook handler needs to distinguish three "no data" cases so it
    can emit appropriate telemetry (``outcome="empty-payload"`` vs
    ``outcome="oversized"`` vs ``outcome="tty"``) without re-detecting
    the case from the absence of bytes alone.
    """

    __slots__ = ("data", "outcome")

    def __init__(self, data: bytes | None, outcome: str) -> None:
        self.data = data
        self.outcome = outcome

    def __bool__(self) -> bool:  # truthy iff bytes present
        return self.data is not None and len(self.data) > 0


def read_hook_stdin(max_bytes: int = DEFAULT_HOOK_STDIN_MAX_BYTES) -> HookStdinResult:
    """Read JSON-ish payload from a Claude Code hook's stdin.

    Returns a :class:`HookStdinResult`. The handler inspects ``.outcome``
    to log telemetry and ``.data`` for the bytes (UTF-8 decodable).

    Outcomes:

    * ``"ok"`` — bytes present, under the cap.
    * ``"empty-payload"`` — stream closed with zero bytes (Claude Code
      under some race conditions emits an empty PostToolUse).
    * ``"tty"`` — stdin is a TTY, meaning a human ran the hook command
      directly to debug. The hook would otherwise hang forever waiting
      for input.
    * ``"oversized"`` — payload exceeded ``max_bytes``. The bytes read
      so far are returned (truncated) so the orphan-dump path can still
      preserve what we got.

    Documented as the canonical pattern for any future stdin-reading
    hook (SubagentStop, etc.) — keeps the four-case handling in one
    place rather than re-discovered per hook.
    """
    try:
        if sys.stdin.isatty():
            return HookStdinResult(None, "tty")
    except (AttributeError, OSError):
        # No stdin at all (e.g. detached subprocess) — treat as TTY for
        # behavioural purposes (no payload to read).
        return HookStdinResult(None, "tty")

    buf = sys.stdin.buffer
    chunk = buf.read(max_bytes + 1)  # +1 to detect overflow without an extra syscall
    if chunk is None:  # closed stream on some weird wrappers
        return HookStdinResult(None, "empty-payload")
    if len(chunk) == 0:
        return HookStdinResult(None, "empty-payload")
    if len(chunk) > max_bytes:
        return HookStdinResult(chunk[:max_bytes], "oversized")
    return HookStdinResult(chunk, "ok")
