"""Shared git auto-commit / auto-push for filed session notes.

Lifted out of ``session_curator.py`` so the buffer-and-flush worker
(``synthesis.synth_and_close``) can run the same opportunistic add+commit
sequence Phase 1 of flush wants without importing the curator entry
point. All failures are logged via ``logger.emit("warning", ...)`` and
never raise — auto-commit/push is opportunistic; the curator's
correctness contract is the ledger / buffer state, not the git state.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_core.session_writer import FiledNote
from lore_core.wiki_config import load_wiki_config

if TYPE_CHECKING:
    from lore_core.run_log import RunLogger


__all__ = ["maybe_auto_commit"]


def maybe_auto_commit(
    wiki_dir: Path,
    filed: FiledNote,
    logger: "RunLogger | None" = None,
    *,
    llm_client: Any = None,
) -> None:
    """Git-add + commit (and optionally push) ``filed`` per wiki config.

    Sequence on a configured wiki:
      1. ``auto_commit=true``  -> add + commit the filed note
      2. ``auto_push=true``    -> push (with LLM-merge on surface conflicts
                                   if ``llm_client`` is provided)
    """
    cfg = load_wiki_config(wiki_dir)
    if not cfg.git.auto_commit:
        return
    if not (wiki_dir / ".git").exists():
        return
    try:
        rel = filed.path.resolve().relative_to(wiki_dir.resolve())
        subprocess.run(
            ["git", "add", str(rel)],
            cwd=str(wiki_dir), capture_output=True, timeout=10, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"lore: {filed.path.stem}"],
            cwd=str(wiki_dir), capture_output=True, timeout=10, check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        if logger is not None:
            logger.emit("warning", message=f"auto-commit failed: {exc}")
        return

    if cfg.git.auto_push:
        try:
            from lore_core.git_sync import SyncStatus, auto_push

            result = auto_push(wiki_dir, llm_client=llm_client)
            if logger is not None:
                if result.status is SyncStatus.MERGE_BLOCKED:
                    logger.emit(
                        "warning",
                        message=f"auto-push merge blocked: {result.message}",
                        blocked_paths=result.blocked_paths,
                    )
                elif result.status not in (SyncStatus.OK, SyncStatus.NOOP, SyncStatus.MERGED):
                    logger.emit(
                        "warning",
                        message=f"auto-push skipped: {result.status.value} ({result.message})",
                    )
        except Exception as exc:  # noqa: BLE001 - push must never abort the curator
            if logger is not None:
                logger.emit("warning", message=f"auto-push failed: {exc}")
