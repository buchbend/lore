"""lore_core — the deterministic core of Lore.

Linter, schema validation, catalog generation, git helpers, atomic I/O.
Invoked by skills, CLI, and MCP server. No LLM dependencies.
"""

from lore_core.config import LORE_ROOT, WIKI_ROOT, get_lore_root, get_wiki_root
from lore_core.schema import (
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    parse_frontmatter,
)

__all__ = [
    "LORE_ROOT",
    "WIKI_ROOT",
    "get_lore_root",
    "get_wiki_root",
    "REQUIRED_FIELDS",
    "SCHEMA_VERSION",
    "parse_frontmatter",
]

__version__ = "0.1.0"
