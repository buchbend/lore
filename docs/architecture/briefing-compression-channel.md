# Briefings as a compression channel

**Audience:** contributors extending `gather()`, `render_briefing()`, or
`compose_briefing_prose()` in `lore_core/briefing/`.

> **Status: parked.** The chain below has session notes as its middle
> stage, and the compose pipeline that wrote them retired in `#361`.
> `gather()` still reads `<wiki>/sessions/`, so it now sees only what a
> human files there. What a briefing should read instead — the transcript
> ledger, accepted flags, or something else — is an open decision, not
> something this page should guess. PRD 0011 parks briefings pending it.
> Everything below describes the code as it stands.

## The drill-down chain

A briefing is not a standalone artifact — it's the entry point of a
chain a teammate can walk from a two-line digest down to the exact
commit that motivated it:

```
briefing → session notes → ADRs/PRDs/issues → code
```

Each stage compresses less and costs more to read. The briefing itself
compresses hardest (one bullet per project/theme); a session note is
the next level of detail; the ADR/PRD/issue it links to is more detail
still; the code is the ground truth. A teammate reads only as far down
the chain as the task requires — most days, the briefing alone is
enough to know "what's Alice been doing on epic #162."

## Linkage is the join key

The chain only holds together if each stage carries pointers to the
next one. Session notes already carry `linkage` frontmatter (`author`,
`repo`, `branch`, `issues`, `prs`, `epics` — see
`lore_core/linkage.py`). The transcript ledger carries an equivalent
block, stamped by capture with no LLM call. `gather()`
(`lore_core/briefing/gather.py`) passes that `linkage` dict through
verbatim on every entry, plus an optional `epic` filter so a caller can
scope gather to one epic's sessions directly.

Both consumers of `gather()`'s output turn `linkage` into drill-down
links:

- `render_briefing()` (`format.py`) — the deterministic fallback —
  appends a `[[note-slug]]` wikilink and `(author · epic #N · #issue)`
  refs to every bullet.
- `compose_briefing_prose()` (`compose.py`) — the LLM path — folds the
  same refs into each session's line in the prompt, so the model can
  key its "What happened" bullets by author/epic instead of just
  chronology.

This is a pure join on frontmatter, no LLM involved in the linking
itself — consistent with [[why-tdd-is-enforced]]'s broader stance that
retrieval in this codebase is deterministic, never ranked guesswork
dressed up as relevance.

## Why pull-only

Briefings publish outward (see `docs/how-to/matrix-bot.md` for the
sink recipe) but `gather()` itself never writes anything — no ledger
mutation, no note edits. A teammate reading a briefing pulls further
detail by following a wikilink or an issue ref; nothing pushes context
at them beyond the one digest. Keeps the channel cheap to run
lights-out and keeps the vault the only source of truth for what a
link actually points to.
