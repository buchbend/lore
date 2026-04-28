# `lore drill` — composite multi-stage retrieval

**Audience:** contributors implementing or extending the `lore drill`
MCP tool and the `lore drill` CLI presenter.

This document settles **P3.2** from the 2026-04-27 multi-agent review.
`lore drill` does not exist yet — this is the design before the first
implementation commit.

## Purpose

`lore_search` returns ranked paths. To go from there to "the actual
context I want" the model often runs `search → read top hits → expand
wikilinks → read the linked notes`. Today every step is a separate
MCP call: more round-trips, more token overhead in tool envelopes,
more places to drop the thread.

`lore drill <query>` collapses the chain into one call.

## The composite-with-trace shape (settled)

One MCP call, one envelope, structured trace alongside the result:

```json
{
  "trace": [
    {"stage": "search", "query": "...", "hits": 7, "elapsed_ms": 12},
    {"stage": "read",   "paths": ["wiki/foo.md", "wiki/bar.md"], "elapsed_ms": 23},
    {"stage": "expand", "wikilinks": ["[[baz]]", "[[qux]]"], "elapsed_ms": 18},
    {"stage": "read_expanded", "paths": ["wiki/baz.md"], "elapsed_ms": 9}
  ],
  "result": {
    "notes": [ /* { path, frontmatter, body } per note */ ]
  }
}
```

**Properties:**

- One round-trip — the cost win that motivated composite over a
  per-stage chain.
- Trace surfaces in both the LLM tool result and the human transcript,
  so retrieval failures stay debuggable without re-running with
  logging.
- Single index snapshot — no consistency surprises across stages
  (which the per-stage chain would inherit if the MCP server
  reindexes mid-chain).

**What you give up vs per-stage calls:** the model can't make
mid-chain decisions ("search returned junk, re-query before reading").
For `drill` that's the correct trade — when the model wants to steer,
it falls back to calling `lore_search` → `lore_read` itself.

## Server-side short-circuit

Empty intermediate results skip downstream stages and record the skip
in the trace:

```json
{"stage": "expand", "skipped": "no_wikilinks"}
{"stage": "read", "skipped": "search_returned_zero"}
```

Skip codes documented on the tool-schema docstring — the LLM uses them
to know whether to broaden the query or accept the empty result.

## Stage order and limits

1. `search` — top-k hits via FTS. `k` defaults to 5, configurable per
   call.
2. `read` — read every hit's full body + frontmatter.
3. `expand` — collect outbound wikilinks across all read notes,
   deduplicated.
4. `read_expanded` — read the expanded set, capped at `expand_limit`
   (default 5) to bound envelope size.

The cap matters: a hub note might link 50 places. `read_expanded`
truncates and records the truncation in the trace
(`{"truncated": 47, "kept": 5}`).

## CLI presentation

`lore drill <query>` (Typer command, mounted via the cli-contract
pattern) calls the same handler as the MCP tool, then renders the
trace as a tree (rich) and the result as a paginated note list. No
special-casing — the trace ships in the response either way; the CLI
just chooses to format it visibly.

## Trace contract for clients

The trace shape is part of the MCP response and is now public. Clients
should:

* Always check ``"skipped" in step`` before reading ``paths`` /
  ``wikilinks`` — skipped stages omit the data keys.
* Treat ``elapsed_ms`` as always present and always an int.
* Use ``expand.wikilinks`` to answer "which wikilinks were
  *discovered*" and ``read_expanded.paths`` for "which were
  *read*". They diverge when a wikilink doesn't resolve to a real
  note or when the read fails — in that case the slug appears in
  ``expand.wikilinks`` only.
* ``read.read_failed`` and ``read_expanded.read_failed`` (optional
  keys) surface paths whose ``handle_read`` returned an error. The
  presence of a path here means it's listed under ``paths`` but its
  body is **not** in ``result.notes``.
* ``read_expanded.truncated`` / ``kept`` only appear when the
  ``expand_limit`` cap actually stopped the loop. A smaller resolved
  set than ``expand_limit`` (because slugs were unresolvable, not
  because the cap fired) will *not* show truncation.

## Known limitations

* **Catalog ambiguity.** ``_resolve_slug`` falls back to ``rglob`` and
  returns the first match if the catalog doesn't carry the slug. Two
  notes with the same filename → drill silently picks one. Drill
  amplifies this versus ``lore_read`` because it walks the link graph.
  Inherited from the existing read path; not a drill-specific bug.

## Out of scope (for the first ship)

- Re-ranking with embeddings — punt to a future flag once the basic
  chain is in place.
- Cross-wiki drilling — first ship is single-wiki, like `lore_search`.
- Caching the trace — the round-trip is cheap; cache complicates the
  consistency story.
