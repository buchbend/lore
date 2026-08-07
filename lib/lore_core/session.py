"""Session-note helpers — slug, attach lookup, commit.

Lore writes no session note automatically; the compose pipeline that once
filed one was retired. This module keeps only the pieces other modules
still need:

* :func:`_resolve_attach_block` — attach lookup used by hooks/doctor
* :func:`slugify` — slug derivation
* :func:`commit_note` — used by ``lore session commit`` (also called by
  the inbox + briefing skills to commit non-session files inside a wiki)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def _resolve_attach_block(cwd: Path) -> tuple[Path, dict] | None:
    """Registry-backed lookup for the attachment covering ``cwd``.

    Returns:
      * ``(synthetic_claude_md_path, {"wiki": ..., "scope": ..., ...})``
        for attached cwds (the block dict merges the attachment + any
        ``.lore.yml`` at the repo root, so fields like ``backend``,
        ``issues``, ``prs`` surface to callers that need them), or
      * ``None`` for unattached cwds.
    """
    from lore_core.offer import parse_lore_yml, FILENAME as LORE_YML
    from lore_core.scope_resolver import resolve_scope

    scope = resolve_scope(cwd)
    if scope is None:
        return None
    block = {"wiki": scope.wiki, "scope": scope.scope, "backend": scope.backend}

    # Attachment paths store the repo root; look for a `.lore.yml` there
    # and merge its non-routing fields (backend, issues, prs, wiki_source)
    # into the block dict. This keeps downstream callers
    # (`_session_start_from_lore`, etc.) working unchanged.
    repo_root = scope.claude_md_path.parent
    offer = parse_lore_yml(repo_root / LORE_YML)
    if offer is not None:
        if offer.backend:
            block["backend"] = offer.backend
        if offer.issues:
            block["issues"] = offer.issues
        if offer.prs:
            block["prs"] = offer.prs
        if offer.wiki_source:
            block["wiki_source"] = offer.wiki_source

    return scope.claude_md_path, block


_SLUG_NONWORD = re.compile(r"[^\w\s-]")
_SLUG_DASH = re.compile(r"[\s_-]+")


def slugify(text: str) -> str:
    """Kebab-case slug from arbitrary text. Caps at 60 chars."""
    s = _SLUG_NONWORD.sub("", text.lower())
    s = _SLUG_DASH.sub("-", s).strip("-")
    return s[:60]


def commit_note(
    *,
    wiki_path: Path,
    note_path: Path,
    message: str | None = None,
) -> tuple[bool, str]:
    """``git add`` + ``git commit`` in the wiki repo.

    Returns ``(success, sha-or-error)``. Idempotent for already-committed
    state (``"nothing to commit"`` returns ``True`` with empty sha).

    The commit carries a pathspec, so only ``note_path`` lands in it. A
    flag write calls this on every write, and a wiki is a directory a
    human also edits — whatever else they staged stays staged.
    """
    rel = note_path.resolve().relative_to(wiki_path.resolve())
    add = subprocess.run(
        ["git", "add", str(rel)],
        cwd=str(wiki_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        return False, add.stderr.strip() or "git add failed"
    if message is None:
        slug = note_path.stem
        message = f"lore: session {slug}"
    commit = subprocess.run(
        ["git", "commit", "-m", message, "--", str(rel)],
        cwd=str(wiki_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        if "nothing to commit" in commit.stdout or "nothing to commit" in commit.stderr:
            return True, ""
        return False, commit.stderr.strip() or commit.stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(wiki_path),
        capture_output=True,
        text=True,
        check=False,
    )
    return True, head.stdout.strip() if head.returncode == 0 else ""
