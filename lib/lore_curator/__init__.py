"""lore_curator — the curator triad (session / daily / defrag).

Three curators with distinct cadences and responsibilities. The
modules carry role names; a legacy A/B/C label persists in code
identifiers for backward compat with the ~188 call sites across
``lib/`` and ``tests/`` that imported the old names.

- **Session curator** (``session_curator.py``, formerly ``curator_a.py``)
  — files session notes from completed transcripts. Per-session-end
  cadence. Entry point: ``run_session_curator`` (alias of
  ``run_curator_a``).
- **Daily curator** (``daily_curator.py``, formerly ``curator_b.py``)
  — extracts concept surfaces and regenerates ``threads.md``.
  Per-day-rollover cadence. Entry point: ``run_daily_curator``
  (alias of ``run_curator_b``).
- **Defrag curator** (``defrag_curator.py``, formerly ``curator_c.py``)
  — weekly defrag / stale-flag / supersession / orphan-link repair.
  Per-week cadence, SessionStart-triggered via time + global lock.
  Entry point: ``run_defrag_curator`` (alias of ``run_curator_c``).

See ``docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md``
for the pipeline design and ``project_curator_triad`` /
``project_lore_heartbeat`` memory entries for cadence and trigger
model.
"""

from lore_curator.defrag_curator import main, run_curator_c, run_defrag_curator

__all__ = ["main", "run_curator_c", "run_defrag_curator"]

