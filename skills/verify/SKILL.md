---
name: lore:verify
description: List pending freshness verdicts and let the user resolve
  each one. The primary verdict path is the in-passing nudge emitted
  during a session; this slash command is the explicit fallback that
  makes the SessionStart pending-verdict chip actionable. Run with
  "/lore:verify" for the current session's flagged notes,
  "/lore:verify --all" for every currently-stale-candidate note in
  the active wiki.
user_invocable: true
---

# Pending freshness verdict resolver

Walk the user through resolving the freshness backlog. Slice 10 of
PRD #65 — secondary path; the primary verdict path is the LLM-emitted
in-passing nudge.

## Workflow

1. **Resolve scope.**
   - Bare `/lore:verify` — flagged notes touched in the current session
     per Curator A's activity log.
   - `/lore:verify --all` — every currently-stale-candidate note across
     the active wiki.

2. **Discover pending notes.** Read `_catalog.json` (a single fs read,
   no walk) and filter for entries with `status: stale`,
   `superseded_by`, or membership in the cached `orphan_set`. These are
   the candidates. For the bare form, intersect with the activity log
   from the most recent session note's `files_read` / `files_modified`
   frontmatter.

3. **For each candidate, build a picker entry:**
   - Slug, cause (`authored_marker` / `orphan_broken`), short reason
     from the note's frontmatter.
   - Most recent personal `confirmed_at` from
     `wiki/<name>/_verdicts/<handle>.json` if any.
   - When the freshness block carries `disagreement`, render the
     stale-by / stale-at / self-confirmed-at fields verbatim.

4. **Show one picker at a time** (use AskUserQuestion). Options:
   - `confirm` → call `mcp__lore__lore_verdict` with
     `{verdict: "confirm", wiki, note}`. No reason required.
   - `stale` → prompt the user for a one-line reason (required by the
     stale verdict contract, slice 5), then call `mcp__lore__lore_verdict`
     with `{verdict: "stale", wiki, note, reason}`.
   - `skip` → make no write at all. Silence semantics preserved at
     this surface too.

5. **After every verdict, echo the new freshness block** so the user
   sees exactly what changed.

6. **At the end of the loop**, summarize: how many confirmed, how many
   stale, how many skipped. Mention that the SessionStart status-line
   chip will reflect the new count on the next refresh (slice 8).

## Hard rules

- One MCP call per verdict — do not batch (each verdict is a separate
  user choice and the per-write audit trail matters).
- Never auto-resolve a `disagreement` — always ask.
- `skip` means *make no write*; do not even open the sidecar.
- Never modify the note body. The verdict contract is frontmatter-only
  (slice 5) plus the per-user sidecar (slice 6); both are additive.
- If the user has zero pending verdicts in scope, say so and stop —
  do not invent work.
