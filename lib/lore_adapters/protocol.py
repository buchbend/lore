"""Adapter protocol — how every host adapter speaks to lore.

Downstream components (curator, ledger, CLI) speak only `Turn` +
`TranscriptHandle` — never the host's native format. This seam makes
lore host-agnostic and allows third-party adapters.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

from lore_core.types import Turn, TranscriptHandle


@runtime_checkable
class Adapter(Protocol):
    """Protocol every host adapter implements.

    Downstream components (curator, ledger, CLI) speak only `Turn` +
    `TranscriptHandle` — never the host's native format. This is the
    seam that makes lore host-agnostic.

    Implementations: `claude-code` (Task 6), `manual-send` (Task 7).
    Further hosts (codex, opencode, copilot-cli, gemini-cli) plug in
    via the registry (Task 8) without changing downstream code.

    Note on Turn.host_extras: currently debug-only; no registry of
    recognised keys. When third-party adapters land, tighten this
    before allowing specialist passes to peek.
    """

    host: str

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        """Return transcripts whose session cwd equals `directory`."""
        ...

    def read_slice(
        self,
        handle: TranscriptHandle,
        from_index: int = 0,
    ) -> Iterator[Turn]:
        """Stream turns with `turn.index >= from_index`."""
        ...

    def read_slice_after_hash(
        self,
        handle: TranscriptHandle,
        after_hash: str | None,
        index_hint: int | None = None,
    ) -> Iterator[Turn]:
        """Stream turns after the turn whose `content_hash()` == `after_hash`.

        Starts at `index_hint` if provided; verifies the hash at that
        position; falls back to content scan on mismatch. If
        `after_hash` is None, streams from the beginning.

        Handles host-side mutation of prior turns (e.g., Cursor's
        SQLite store may rewrite earlier messages): the hint is
        advisory; the hash is the authoritative anchor.
        """
        ...

    def is_complete(self, handle: TranscriptHandle) -> bool:
        """True if the transcript's session has ended (vs. being actively written)."""
        ...
