"""Opportunistic entry point for the retention janitor (issue #190).

``lore_core.janitor.run_janitor`` covers everything scoped to one
``lore_root/.lore/`` (spine tiers, run-archival, flush store); this
composer adds the two families that live outside that layering — crash
logs under the global ``$LORE_CACHE`` (``lore_cli._crash_log``) and drain
orphan pruning (``lore_cli.drain_cmd``, owned by the CLI layer) — and is
what hook fire / curator run end actually call.

Best-effort like every other opportunistic hook-path call
(``_gc_sessions_cache`` is the existing precedent): never raises, so a
janitor bug can't take down a hook or a curator run.
"""

from __future__ import annotations

from pathlib import Path


def run_opportunistic_janitor(lore_root: Path) -> None:
    try:
        from lore_core.janitor import run_janitor
        from lore_core.root_config import load_root_config

        cfg = load_root_config(lore_root)
        run_janitor(lore_root, cfg.observability)

        from lore_cli._crash_log import purge_old_crashes

        purge_old_crashes(cfg.observability.retention.crash_log_days, lore_root=lore_root)

        from lore_cli.drain_cmd import prune_orphans

        prune_orphans(lore_root)
    except Exception:
        pass
