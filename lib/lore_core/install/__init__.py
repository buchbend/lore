"""Per-host installer modules + shared helpers.

Each `<host>` Python module in this package exposes:

    SCHEMA_VERSION: str           # bump when the managed-block shape changes
    plan(ctx) -> list[Action]     # actions to install/upgrade Lore for this host
    detect_legacy(ctx)            # install.sh-era artifacts this host left behind
                                  #   (typed as list[LegacyArtifact])

The dispatcher (`lib/lore_cli/install_cmd.py`) imports REGISTRY here
to discover available hosts, then calls per-host `plan()` and
`detect_legacy()` and renders the actions through the print-and-confirm
UI.

No abstraction layer — adding a new host means dropping a single
Python module here and wiring its name into REGISTRY. Composition
patterns ("strategy" registries, plugin protocols) get added the
day a third host needs them.
"""

from __future__ import annotations

from lore_core.install import claude, cursor

REGISTRY: dict[str, object] = {
    "claude": claude,
    "cursor": cursor,
}


def known_hosts() -> list[str]:
    return sorted(REGISTRY)


def get_host(name: str) -> object | None:
    return REGISTRY.get(name)
