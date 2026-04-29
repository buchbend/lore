"""Producer-keyed adapters for tool-specific hook payload shapes.

Adapters translate a producer's hook-payload dialect into raw markdown
(plus a telemetry tag identifying which field matched). Once they
return markdown, classification proceeds through
:mod:`markdown_adapter` like any other markdown source.

Each adapter implements the :class:`Adapter` protocol below. The
:data:`ADAPTERS` registry maps producer name strings (``"claude-code"``,
``"cursor"``, …) to instances; :func:`dispatch` selects one based on
:class:`IngestSource.producer` and falls back to the
``claude-code`` adapter when an unknown producer is named (most
unknown producers ship Claude-Code-shaped JSON payloads in practice).

To add a new producer:

* Drop a module under ``adapters/`` that exposes either an ``Adapter``
  instance or two free functions (``detect``, ``extract``).
* Register it in :data:`ADAPTERS`.

The legacy free function ``parser.parse_payload`` is preserved as a
shim that delegates to the ``claude-code`` adapter.
"""
from __future__ import annotations

from typing import Any, Protocol


class Adapter(Protocol):
    """Hook-payload adapter contract.

    Adapters are stateless; instances are typically module-level
    singletons. Implementations may be objects or modules — anything
    with the two methods works.
    """

    def detect(self, payload: dict) -> bool:
        """Return True if this adapter recognizes ``payload``.

        Used as a fallback discriminator when ``IngestSource.producer``
        is unset or unknown. Implementations should be cheap (key
        presence checks) since :func:`dispatch` may call ``detect`` on
        every registered adapter in the worst case.
        """
        ...

    def extract(self, payload: dict) -> tuple[str | None, str]:
        """Return ``(markdown_text, source_field)``.

        ``markdown_text`` is None when the adapter found no plan body
        in ``payload``. ``source_field`` is a short string identifying
        which input field the markdown came from (logged for telemetry).
        """
        ...


from . import claude_code as _claude_code  # noqa: E402

#: Registry of producer name → adapter. Add new entries here when new
#: AI tools ship plan-mode hooks with their own payload shapes.
ADAPTERS: dict[str, Adapter] = {
    "claude-code": _claude_code,
    # Future: "cursor": _cursor, "aider": _aider, …
}


def dispatch(producer: str) -> tuple[Adapter, bool]:
    """Return ``(adapter, is_known)`` for ``producer``.

    Falls back to ``claude-code`` when ``producer`` isn't registered;
    this lets unknown producers (custom CI scripts emitting
    Claude-Code-shaped JSON) work transparently. ``is_known`` is False
    when the fallback fired so the caller can attach an
    ``unknown_producer`` warning to the :class:`IngestResult` rather
    than silently misclassifying a typo (``"curser"`` vs ``"cursor"``).
    """
    if producer in ADAPTERS:
        return ADAPTERS[producer], True
    return _claude_code, False


__all__ = ["ADAPTERS", "Adapter", "dispatch"]
