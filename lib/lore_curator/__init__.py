"""lore_curator — per-wiki maintenance (staleness, supersession, catalogs).

Phase A scope. Runs on-schedule or on-push. Frontmatter-only edits by
default; body edits require user approval. Guards against concurrent
Obsidian edits via mtime checks.

Not implemented in v0.1.
"""
