"""lore_sinks — briefing sink adapters.

Each adapter publishes a markdown briefing to its target (Matrix, Slack,
Discord, markdown file, GitHub Discussion). All adapters share the same
CLI surface:

    python -m lore_sinks.<name> send --file <path>
    python -m lore_sinks.<name> send   # stdin
"""

from __future__ import annotations

from typing import Protocol


class BriefingSink(Protocol):
    """Interface every sink adapter implements."""

    name: str

    def send(self, text: str) -> None:
        """Publish the briefing text. Raise on failure."""
        ...
