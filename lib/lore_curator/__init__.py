"""lore_curator — the A/B/C curator triad.

Three curators with distinct cadences and responsibilities:

- Curator A (``curator_a.py``): session notes — per-session-end cadence.
- Curator B (``curator_b.py``): surface extraction — per-day-rollover cadence.
- Curator C (``curator_c.py``): weekly defrag / stale-flag / supersession —
  per-week cadence, SessionStart-triggered via time + global lock.

See ``docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md``
for the pipeline design and ``project_curator_triad`` / ``project_lore_heartbeat``
memory entries for cadence and trigger model.
"""

from lore_curator.curator_c import main, run_curator_c

__all__ = ["main", "run_curator_c"]

