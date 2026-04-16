"""lore_import — migrate existing markdown vaults to Lore's shape.

Phase C scope. Two modes in v1:
- mount-as-is (default, no action required beyond the symlink)
- --enrich (per-note LLM pass: infer type, description, tags, backfill
  created / last_reviewed from git log)

Not implemented in v0.1.
"""
