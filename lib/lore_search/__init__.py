"""lore_search — SQLite FTS5 hybrid retrieval (Model2Vec embedding layer optional).

Ships the default `FtsBackend` implementation of the `LoreBackend`
protocol. Alternate backends (Qdrant, Chroma, etc.) can swap in without
forking by implementing the same protocol.
"""

from lore_search.backend import LoreBackend, SearchHit
from lore_search.fts import FtsBackend

__all__ = ["LoreBackend", "SearchHit", "FtsBackend"]

