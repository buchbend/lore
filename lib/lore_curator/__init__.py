"""lore_curator — per-wiki maintenance that keeps auto-inject trustworthy.

Flags stale notes (> 90d since last_reviewed), detects superseded
decisions from `supersedes [[X]]` refs, backfills missing `created` /
`last_reviewed` from git log. Frontmatter-only edits; mtime guard
against Obsidian edit races.
"""

from lore_curator.core import main, run_curator

__all__ = ["main", "run_curator"]

