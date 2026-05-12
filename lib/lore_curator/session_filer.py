"""Curator-A side helpers — slug, handle resolution, re-exported activity primitives.

Historical note: this module used to own the legacy classify-per-chunk
``file_session_note`` entry point that bridged ``NoteworthyResult`` →
``SessionInput`` → ``lore_core.session_writer.file_or_merge``. PR 6b
of the streamlining track (issue #80) deleted that path along with
``summary_merge.merge_descriptions`` and the LLM-merged-summary closure.
The buffer-and-flush path (``buffer_append`` + ``stub_note`` +
``synthesis``) is now the only curator-A surface.

What stays here:

* ``_slug`` — title → filename slug; called by ``stub_note``,
  ``synthesis``, and ``backfill_slugs``.
* ``_resolve_handle_for`` — cwd → wiki-canonical author handle; called
  by ``session_curator._process_chunk_buffer_flush``.
* Re-exports of turn-deterministic activity helpers from
  ``session_activity``. Tests pre-dating the move still import them
  through this module; new code should import from
  ``lore_curator.session_activity`` directly.
"""
from __future__ import annotations

import re
from pathlib import Path

from lore_core.types import TranscriptHandle
# Turn-deterministic activity extraction lives in session_activity. The
# leading-underscore re-exports preserve the legacy import paths used
# throughout the test suite; new code (buffer_append, stub_note) should
# import from session_activity directly.
from lore_curator.session_activity import (
    _COMMIT_SHA_LINE_RE,  # noqa: F401  (test back-compat)
    _FILE_PATH_INPUT_KEYS,  # noqa: F401  (test back-compat)
    _all_turn_text,  # noqa: F401  (test back-compat)
    _collect_activity,  # noqa: F401  (test back-compat)
    _commit_shas_from_bash_results,  # noqa: F401  (test back-compat)
    _file_path_from_tool_input,  # noqa: F401  (test back-compat)
    _files_modified_from_turns,  # noqa: F401  (test back-compat)
    _files_read_from_turns,  # noqa: F401  (re-export for callers needing read-only paths)
    _files_touched_from_turns,  # noqa: F401  (test back-compat)
    _is_git_commit_command,  # noqa: F401  (test back-compat)
    collect_commits_by_sha,  # noqa: F401  (monkeypatch surface for tests)
    collect_issues_in_window,  # noqa: F401  (monkeypatch surface for tests)
)


__all__ = ["_slug", "_resolve_handle_for"]


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 60


def _slug(title: str) -> str:
    """Lowercase, hyphen-separated, alphanumeric-only; smart-truncated.

    When the cleaned title exceeds ``_SLUG_MAX`` chars, truncate at the last
    hyphen boundary that keeps the slug within the limit so we never cut a
    word in half (the old hard ``[:60]`` produced filenames like
    "...rebase-onto-pha"). If no boundary fits — pathological case where the
    title is one giant unbroken alphanumeric blob — fall back to a hard cut.
    """
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    if not s:
        return "session"
    if len(s) <= _SLUG_MAX:
        return s
    truncated = s[:_SLUG_MAX]
    last_dash = truncated.rfind("-")
    if last_dash > 0:
        return truncated[:last_dash]
    return truncated


def _resolve_handle_for(wiki_root: Path, handle: TranscriptHandle) -> str:
    """Return the canonical author handle for this transcript's cwd.

    Passive-capture doesn't carry the author identity on the transcript
    envelope; we resolve it lazily from the working repo's git config.
    Empty string in solo wikis is fine — the writer just skips sharding.
    """
    from lore_core.identity import resolve_handle

    from lore_core.git import git_user_email

    email = git_user_email(handle.cwd, env_override=None)
    return resolve_handle(wiki_root, email) if email else ""
