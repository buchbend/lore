"""lore_search — SQLite FTS5 hybrid retrieval (Model2Vec embedding layer optional).

Ships the default `FtsBackend`. The `LoreBackend` Protocol that lived
here through 0.10.4 was deleted as part of 0.10.5 — single implementer,
zero typed consumers, the abstraction wasn't earning its keep. When
a second backend lands (Qdrant, Chroma), reintroduce the Protocol
*with* the second implementer in the same patch.
"""

from lore_search.fts import FtsBackend, SearchHit

__all__ = ["FtsBackend", "SearchHit"]
