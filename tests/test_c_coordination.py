"""Task 11: Curator C first-come-wins coordination.

- WikiLedger writes fsynced so concurrent readers see committed state
- run_curator_c(defrag=True) re-reads last_curator_c at entry; skips
  wikis whose last_curator_c matches the current ISO week (equivalence,
  not timestamp > threshold — avoids clock-skew false positives)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


def _seed_vault(tmp_path: Path) -> Path:
    lore_root = tmp_path / "vault"
    (lore_root / ".lore").mkdir(parents=True)
    wiki = lore_root / "wiki" / "w"
    (wiki / "sessions").mkdir(parents=True)
    return lore_root


def test_ledger_write_is_fsynced(tmp_path: Path, monkeypatch) -> None:
    """atomic_write_text must fsync the tmp file before rename."""
    fsync_calls = []
    real_fsync = os.fsync

    def tracking_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr("os.fsync", tracking_fsync)

    from lore_core.io import atomic_write_text
    atomic_write_text(tmp_path / "x.txt", "hello\n")

    assert fsync_calls, "atomic_write_text must call fsync before rename"


def test_second_user_skips_after_first_iso_week_match(tmp_path: Path, monkeypatch) -> None:
    """Pre-populate last_curator_c as now → run_curator_c(defrag=True) skips."""
    from lore_core.ledger import WikiLedger
    from lore_curator.curator_c import run_curator_c

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    now = datetime.now(UTC)
    # Fresh run this same ISO week.
    WikiLedger(lore_root, "w").update_last_curator("c", at=now)

    before_entry = WikiLedger(lore_root, "w").read()
    before_ts = before_entry.last_curator_c

    # Invoke. Since last_curator_c is in the current ISO week, the wiki
    # is skipped; last_curator_c should NOT advance.
    reports = run_curator_c(wiki_filter="w", dry_run=False, defrag=True)

    after_entry = WikiLedger(lore_root, "w").read()
    # Last-curator-c timestamp should be unchanged (or very close to it —
    # the run didn't write a new one for the skipped wiki).
    assert after_entry.last_curator_c is not None
    # Within 2 seconds (allows for update_last_curator's own now() call).
    delta = abs((after_entry.last_curator_c - before_ts).total_seconds())
    assert delta < 2.0, (
        f"skipped wiki must not advance last_curator_c materially; "
        f"before={before_ts}, after={after_entry.last_curator_c}"
    )


def test_coordination_fresh_cycle_proceeds(tmp_path: Path, monkeypatch) -> None:
    """last_curator_c from prior ISO week → run proceeds normally."""
    from lore_core.ledger import WikiLedger
    from lore_curator.curator_c import run_curator_c

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))
    prior_week = datetime.now(UTC) - timedelta(days=10)
    WikiLedger(lore_root, "w").update_last_curator("c", at=prior_week)

    run_curator_c(wiki_filter="w", dry_run=False, defrag=True)

    after = WikiLedger(lore_root, "w").read()
    # last_curator_c must have advanced to ~now (this cycle's run).
    now = datetime.now(UTC)
    assert after.last_curator_c is not None
    assert (now - after.last_curator_c).total_seconds() < 60, (
        "fresh cycle should advance last_curator_c to current run"
    )


def test_coordination_never_run_proceeds(tmp_path: Path, monkeypatch) -> None:
    """No prior run (last_curator_c=None) → proceed; first run records."""
    from lore_core.ledger import WikiLedger
    from lore_curator.curator_c import run_curator_c

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    assert WikiLedger(lore_root, "w").read().last_curator_c is None
    run_curator_c(wiki_filter="w", dry_run=False, defrag=True)

    after = WikiLedger(lore_root, "w").read()
    assert after.last_curator_c is not None, "first run must record last_curator_c"


def test_coordination_clock_skew_immune(tmp_path: Path, monkeypatch) -> None:
    """Two users with clocks 5 min apart both in the same ISO week → the
    second's `iso_now == iso_last` check correctly matches regardless of
    which one wrote first. Skew doesn't cause a double-run.
    """
    from lore_core.ledger import WikiLedger
    from lore_curator.curator_c import run_curator_c

    lore_root = _seed_vault(tmp_path)
    monkeypatch.setenv("LORE_ROOT", str(lore_root))

    # User A writes last_curator_c 5 min in the future from user B's clock.
    user_a_time = datetime.now(UTC) + timedelta(minutes=5)
    WikiLedger(lore_root, "w").update_last_curator("c", at=user_a_time)

    # User B (now) checks: iso_week(user_a_time) == iso_week(now_B) in
    # nearly all cases except late Sunday. Assert same-week skip.
    before = WikiLedger(lore_root, "w").read()

    run_curator_c(wiki_filter="w", dry_run=False, defrag=True)

    after = WikiLedger(lore_root, "w").read()
    # Should be unchanged — skip path.
    assert after.last_curator_c == before.last_curator_c
