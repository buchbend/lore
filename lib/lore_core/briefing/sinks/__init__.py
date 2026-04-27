"""Sink registry — scheme → sender callable.

Each sender is a ``Callable[[str, str], None]`` that takes
``(target, text)`` where ``target`` is the URI's post-scheme component
(may be empty for sinks like Matrix that read all config from env vars).

Senders register themselves at import time via ``register(scheme, fn)``.
Built-in sinks live in ``lore_core/briefing/sinks/markdown.py`` and
``matrix.py``; third parties drop a module that calls ``register`` on
import. Future enhancement: scan the ``lore.sinks`` setuptools
entry-point group on first ``dispatch`` call.
"""

from __future__ import annotations

from typing import Callable

Sender = Callable[[str, str], None]

_REGISTRY: dict[str, Sender] = {}


class UnknownSinkError(KeyError):
    """Raised when ``dispatch`` receives a URI whose scheme isn't registered."""


def register(scheme: str, sender: Sender) -> None:
    """Add or replace a sink registration.

    Schemes are URI-style — lowercase ASCII, no colon. The first
    component of a sink URI (``markdown:/path``) is matched case-
    sensitively against this dict.
    """
    _REGISTRY[scheme] = sender


def registered_sinks() -> list[str]:
    """Sorted list of registered sink schemes."""
    return sorted(_REGISTRY)


def dispatch(uri: str, text: str) -> None:
    """Send ``text`` via the sink named by ``uri``'s scheme.

    URI shape: ``"<scheme>"`` or ``"<scheme>:<target>"``. The target is
    everything after the first colon; sinks that don't need a target
    (e.g. Matrix, with config in env vars) receive an empty string.

    Raises ``UnknownSinkError`` if no sink is registered for the scheme.
    Sender exceptions propagate — callers decide whether to swallow
    failures (e.g. Curator B's auto-publish swallows; the explicit
    ``lore briefing publish`` re-raises).
    """
    scheme, _, target = uri.partition(":")
    sender = _REGISTRY.get(scheme)
    if sender is None:
        raise UnknownSinkError(scheme or "(empty)")
    sender(target, text)


# Built-in sinks register themselves on import. Order doesn't matter.
from lore_core.briefing.sinks import markdown, matrix  # noqa: E402, F401
