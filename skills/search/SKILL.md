---
name: search
description: Hybrid ranked search over the knowledge vault (SQLite FTS5 +
  optional Model2Vec embeddings). Returns top-k paths + descriptions.
  Run with "/lore:search <query>".
user_invocable: true
---

# Vault Search

Programmatic search primitive used by `/lore:resume`, `/lore:inbox`, and
the MCP server. Users can also invoke it directly.

## Under the hood

SQLite FTS5 index at `~/.cache/lore/search.db`. Incremental reindex
driven by mtime + SHA256. Model2Vec embedding layer optional (install
the `search` extras to enable). Results use BM25 with title/description/
tag boosts; repo filter becomes a rank boost rather than a hard filter.

## Invocation

```
/lore:search "transaction buffer"
/lore:search "matrix vs slack" --wiki personal
/lore:search "retry logic" --for-repo myorg/data-transfer --k 10
```

Or as a CLI:

```bash
lore search "transaction buffer"
lore search --stats       # index stats
lore search --reindex     # full reindex
```

## When to use this instead of Grep

- Ranked by relevance, not first-match-wins
- Cheap on tokens (top-k with descriptions, not dumps of every match)
- Knows about frontmatter (description and tags boost)
- Repo-aware (`--for-repo` boosts notes tagged with that repo)

## Integration with other skills

- `/lore:resume` calls this for keyword-driven context loading
- `/lore:inbox` calls this for contradiction checks
- MCP server exposes it as `lore_search` to any MCP client
