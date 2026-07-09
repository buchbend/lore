"""Deterministic workflow-script substrate for `lore workflow` (PRD 0003).

Ported near-verbatim from the ccat-agent-workflow plugin's `to-epic` and
`document-epic` skill helpers: no third-party dependencies, no GitHub I/O —
callers supply text/paths they already have. Skills built on these become
thin prose calling into a `lore workflow` subcommand instead of embedding
the mechanic.
"""

from __future__ import annotations
