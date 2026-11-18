"""Lore for scripts: the functions behind the MCP tools, as plain data.

Each function returns dicts and lists and prints nothing. An expected
failure (no wiki, no repo, no such doc) comes back as an ``{"error": ...}``
envelope.

    search(query, wiki=None, for_repo=None, k=5)  wiki hits, one list per source
    read(path, wiki=None, section=None, include_human=False)  one note or `<source>:<id>`
    drill(query, wiki=None, k=5, expand_limit=5, expand_only=None)  search+read+expand
    repo_docs_list(kind, repo_path=None)  the repo's ADRs ("adr") or PRDs ("prd")
    repo_docs_fetch(kind, path, repo_path=None)  one ADR or PRD with its body
    context_pack(cwd=None, branch=None, issue=None, repo_path=None, intent=None)  ranked docs
    codemap(mode, repo_path=None, pattern=None, directory=None, limit=30)  code-map slice

Example::

    "$(dirname "$(readlink -f "$(command -v lore)")")/python" - <<'PY'
    from lore.api import context_pack, repo_docs_fetch
    for path in context_pack(intent="board ledger")["ranked"][:3]:
        print(repo_docs_fetch(path.split("/")[1], path)["title"])
    PY
"""

from lore_mcp.server import handle_codemap as codemap
from lore_mcp.server import handle_context_pack as context_pack
from lore_mcp.server import handle_drill as drill
from lore_mcp.server import handle_read as read
from lore_mcp.server import handle_repo_docs_fetch as repo_docs_fetch
from lore_mcp.server import handle_repo_docs_list as repo_docs_list
from lore_mcp.server import handle_search as search

__all__ = [
    "codemap",
    "context_pack",
    "drill",
    "read",
    "repo_docs_fetch",
    "repo_docs_list",
    "search",
]
