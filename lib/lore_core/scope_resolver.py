"""Scope resolver.

Two paths coexist during the Phase 1–5 transition:

* **Registry** (new, canonical going forward): longest-prefix match on
  ``AttachmentsFile`` paths. No filesystem walk. See
  :func:`resolve_scope_via_registry`.
* **Walk-up** (legacy, back-compat): walk parent directories looking for
  ``CLAUDE.md`` with a ``## Lore`` block. See
  :func:`_legacy_walk_up_resolve`.

:func:`resolve_scope` dispatches between the two based on its
``attachments`` argument: when provided, the registry path is used;
otherwise the legacy walk-up runs. Phase 6 removes the walk-up entirely.
"""

from __future__ import annotations

from pathlib import Path

from lore_core.attach import read_attach
from lore_core.state.attachments import AttachmentsFile
from lore_core.types import Scope


def resolve_scope(
    cwd: Path,
    attachments: AttachmentsFile | None = None,
    *,
    max_depth: int = 8,
) -> Scope | None:
    """Resolve ``cwd`` to a :class:`Scope`, or None if unattached.

    When ``attachments`` is provided, longest-prefix match against the
    host's attachments file is used (new path). Otherwise the legacy
    CLAUDE.md walk-up runs (back-compat during transition). The legacy
    path is removed in Phase 6.
    """
    if attachments is not None:
        return resolve_scope_via_registry(cwd, attachments)
    return _legacy_walk_up_resolve(cwd, max_depth=max_depth)


def resolve_scope_via_registry(cwd: Path, attachments: AttachmentsFile) -> Scope | None:
    """Resolve by longest-prefix match on ``attachments``."""
    match = attachments.longest_prefix_match(cwd)
    if match is None:
        return None
    # Synthetic claude_md_path sentinel: callers that walk up from this
    # path to infer $LORE_ROOT still work. Removed when Scope loses the
    # field in Phase 6.
    return Scope(
        wiki=match.wiki,
        scope=match.scope,
        backend="none",
        claude_md_path=match.path / "CLAUDE.md",
    )


def _legacy_walk_up_resolve(cwd: Path, *, max_depth: int = 8) -> Scope | None:
    """Walk up from ``cwd`` looking for CLAUDE.md with a ``## Lore`` block.

    Returns the nearest :class:`Scope` (child wins over ancestor), or
    None if no attached CLAUDE.md is found within ``max_depth`` levels.
    Retired in Phase 6.

    Phase 5: when ``LORE_NEW_STATE=1`` and the block resolves, also fires
    a best-effort lazy migration into the registry. Idempotent — the
    second firing is a no-op because ``migrate_repo`` sees an existing
    ``.lore.yml``. Never raises past the migration call.
    """
    current = cwd.resolve()
    depth = 0

    while depth < max_depth:
        claude_md_path = current / "CLAUDE.md"
        block = read_attach(claude_md_path)

        if block:
            wiki = block.get("wiki")
            scope = block.get("scope")

            if wiki and scope:
                backend = block.get("backend", "none")
                _maybe_lazy_migrate(claude_md_path.parent)
                return Scope(
                    wiki=wiki,
                    scope=scope,
                    backend=backend,
                    claude_md_path=claude_md_path,
                )

        parent = current.parent
        if parent == current:
            break
        current = parent
        depth += 1

    return None


def _maybe_lazy_migrate(repo_path: Path) -> None:
    """Trigger a one-shot migration of ``repo_path`` into the registry when
    ``LORE_NEW_STATE=1``. Swallows every exception — this is a best-effort
    transition path, never a correctness requirement.
    """
    import os

    if os.environ.get("LORE_NEW_STATE") != "1":
        return
    lore_root_env = os.environ.get("LORE_ROOT")
    if not lore_root_env:
        return
    lore_root = Path(lore_root_env)
    if not lore_root.exists():
        return

    try:
        from lore_core.hook_log import HookEventLogger
        from lore_core.migration import migrate_repo

        result = migrate_repo(repo_path, lore_root=lore_root, dry_run=False)
        if result.action == "migrated":
            try:
                HookEventLogger(lore_root).emit(
                    event="attachments-migrated-lazy",
                    outcome="migrated",
                    detail={"repo_path": str(repo_path)},
                )
            except Exception:
                pass
    except Exception:
        # Migration failure is non-fatal — the legacy resolver already
        # returned the right Scope to the caller.
        pass
