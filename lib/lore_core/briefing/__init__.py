"""Briefing publishing — sink registry + dispatch.

Briefings are markdown summaries Curator B (or `lore briefing publish`)
writes out for human consumption. Each briefing is sent to one or more
*sinks* identified by a URI of the form ``<scheme>:<target>``:

    markdown:/path/to/briefing-YYYY-MM-DD.md
    matrix                 (config from env vars)
    slack:#channel-name    (future)

The dispatch layer is a simple scheme→sender registry. Built-in sinks
(``markdown``, ``matrix``) register themselves at import time. Third
parties can ``register("slack", my_sender)`` after import to add new
sinks without forking; entry-point discovery (``lore.sinks`` group) is
on the roadmap.

Mirrors the adapter registry pattern in ``lore_adapters.registry`` —
same shape, different domain.
"""

from lore_core.briefing.compose import compose_briefing_prose
from lore_core.briefing.format import render_briefing
from lore_core.briefing.gather import gather, mark_incorporated
from lore_core.briefing.sinks import (
    SinkConfigMismatchError,
    UnknownSinkError,
    dispatch,
    register,
    registered_sinks,
)

__all__ = [
    "SinkConfigMismatchError",
    "UnknownSinkError",
    "compose_briefing_prose",
    "dispatch",
    "gather",
    "mark_incorporated",
    "register",
    "registered_sinks",
    "render_briefing",
]
