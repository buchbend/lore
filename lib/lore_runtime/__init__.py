"""lore_runtime — shared runtime helpers used by both the CLI and the
deterministic core layers.

This package exists to break a circular dependency: ``lore_core``,
``lore_curator``, ``lore_mcp``, and ``lore_search`` each ship a
``typer.Typer`` subapp that the top-level ``lore`` CLI mounts, so they
need an argv-translating helper (`argv_main`) and shared run-log
renderers (`run_render`). Hosting those in ``lore_cli`` would mean the
"lower" layers import from the "upper" CLI shell — an inverted
dependency that blocks library-mode use, integration testing without
the typer stack, and any future MCP/HTTP entrypoint.

Treat ``lore_runtime`` as a sibling-tier helper:
``lore_core / lore_curator / lore_mcp / lore_search → lore_runtime``,
and ``lore_cli → lore_runtime`` too. Nothing inside ``lore_runtime``
imports from ``lore_cli``; the layering guard in
``tests/test_layering.py`` enforces the rule.
"""
