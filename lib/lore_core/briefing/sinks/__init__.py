"""Sink registry — scheme → sender callable.

Each sender is a ``Callable[[str, str, dict | None], None]`` that takes
``(target, text, config)`` where:

* ``target`` is the URI's post-scheme component (may be empty for sinks
  like Matrix that read all settings from config or env vars).
* ``text`` is the rendered briefing markdown.
* ``config`` is the parsed ``.lore-briefing.yml`` dict, or ``None`` when
  the caller had no wiki context. Sinks resolve required values with
  precedence: env > config > error (mirroring the OpenAI backend
  pattern in ``root_config.py``).

Senders register themselves at import time via ``register(scheme, fn)``.
Built-in sinks live in ``lore_core/briefing/sinks/markdown.py`` and
``matrix.py``; third parties drop a module that calls ``register`` on
import. Future enhancement: scan the ``lore.sinks`` setuptools
entry-point group on first ``dispatch`` call.
"""

from __future__ import annotations

from typing import Any, Callable

Sender = Callable[[str, str, "dict[str, Any] | None"], None]

_REGISTRY: dict[str, Sender] = {}


class UnknownSinkError(KeyError):
    """Raised when ``dispatch`` receives a URI whose scheme isn't registered."""


class SinkConfigMismatchError(RuntimeError):
    """Raised when ``.lore-briefing.yml`` ``sink:`` disagrees with the URI scheme."""


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


def dispatch(
    uri: str,
    text: str,
    config: dict[str, Any] | None = None,
) -> None:
    """Send ``text`` via the sink named by ``uri``'s scheme.

    URI shape: ``"<scheme>"`` or ``"<scheme>:<target>"``. The target is
    everything after the first colon; sinks that don't need a target
    (e.g. Matrix, with config in YAML or env vars) receive an empty
    string.

    ``config`` is the parsed ``.lore-briefing.yml`` for the wiki being
    published, or ``None`` when the caller had no wiki context (legacy
    env-only path). When set and ``sink:`` is present, it must match
    the URI scheme — otherwise :class:`SinkConfigMismatchError` is
    raised so a stale yaml can't quietly hijack a different sink.

    Raises:
        UnknownSinkError: no sink registered for the URI scheme.
        SinkConfigMismatchError: config['sink'] != uri scheme.

    Sender exceptions propagate — callers decide whether to swallow
    failures (e.g. Curator B's auto-publish swallows; the explicit
    ``lore briefing publish`` re-raises).
    """
    scheme, _, target = uri.partition(":")
    sender = _REGISTRY.get(scheme)
    if sender is None:
        raise UnknownSinkError(scheme or "(empty)")
    if config is not None:
        configured = config.get("sink")
        if configured and configured != scheme:
            raise SinkConfigMismatchError(
                f"sink mismatch: --sink={scheme!r} but "
                f".lore-briefing.yml sets sink={configured!r}"
            )
    sender(target, text, config)


# Built-in sinks register themselves on import. Order doesn't matter.
from lore_core.briefing.sinks import markdown, matrix  # noqa: E402, F401
