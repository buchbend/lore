"""LoreBackend protocol — the contract every search backend implements.

Ship FTS5 + Model2Vec as the default. Swap in Qdrant, Chroma, or any
other index by providing a class that satisfies this protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class SearchHit:
    """One ranked result from a backend query."""

    path: str  # relative to wiki root
    wiki: str
    filename: str
    score: float
    description: str | None = None
    tags: list[str] | None = None
    snippet: str | None = None


class LoreBackend(Protocol):
    """Interface every retrieval backend implements.

    Backends own their own index storage (under ~/.cache/lore/ by
    convention). The linter owns the source-of-truth `_catalog.json`.
    """

    name: str

    def reindex(self, *, wiki: str | None = None) -> int:
        """Full reindex (optionally scoped). Returns number of documents indexed."""
        ...

    def reindex_one(self, path: Path) -> None:
        """Incrementally re-index a single note."""
        ...

    def search(
        self,
        query: str,
        *,
        wiki: str | None = None,
        for_repo: str | None = None,
        k: int = 5,
    ) -> list[SearchHit]:
        """Return top-k ranked results. `for_repo` boosts matching notes."""
        ...

    def stats(self) -> dict:
        """Return index stats (doc count, size, freshness)."""
        ...
