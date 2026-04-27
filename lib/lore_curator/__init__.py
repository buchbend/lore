"""lore_curator — the curator triad (A / B / C).

Three curators with distinct cadences and responsibilities. Internal
identifiers use the A/B/C labels; user-facing copy says simply "Curator"
or the role name (per ``project_curator_triad`` and the
``feedback_curator_naming`` memory entries).

- **Curator A** (``session_curator.py``) — files session notes from
  completed transcripts. Per-session-end cadence. Entry point:
  :func:`run_curator_a`.
- **Curator B** (``daily_curator.py``) — extracts concept surfaces and
  regenerates ``threads.md``. Per-day-rollover cadence. Entry point:
  :func:`run_curator_b`.
- **Curator C** (``defrag_curator.py``) — weekly defrag / stale-flag /
  supersession / orphan-link repair. Per-week cadence,
  SessionStart-triggered via time + global lock. Entry point:
  :func:`run_curator_c`.

The 0.10.x role-name aliases (``run_session_curator``,
``run_daily_curator``, ``run_defrag_curator``) were dropped in 0.12.0:
the rename never stuck — usage skewed 195:11 toward the A/B/C names
and the aliases existed only to soften a transition that didn't
happen. See ``docs/superpowers/specs/2026-04-19-passive-capture-v1-design.md``
for the pipeline design.
"""

from lore_curator.defrag_curator import run_curator_c

__all__ = ["run_curator_c"]
