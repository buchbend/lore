"""Flush worker entry points for the buffer-and-flush curator.

The flush lifecycle is the segmenter + typed-fact extractor + publish gate +
deterministic render over the append-only note document (see
:mod:`lore_curator.chapter_flush`). This module keeps the historical
entry-point names so callers (the curator-A dispatch, the reaper, the
``lore curator flush`` CLI) import from a stable place while the
implementation lives next door.
"""
from __future__ import annotations

from lore_curator.chapter_flush import (
    FlushOutcome,
    SweepReport,
    spawn_detached_flush,
    startup_sweep,
    sweep_dead_sessions,
    synth_and_close,
)

__all__ = [
    "FlushOutcome",
    "SweepReport",
    "synth_and_close",
    "spawn_detached_flush",
    "sweep_dead_sessions",
    "startup_sweep",
]
