---
name: lore:verify
description: List pending freshness verdicts and let the user resolve
  each one. The primary verdict path is the in-passing nudge emitted
  during a session; this slash command is the explicit fallback that
  makes the SessionStart pending-verdict chip actionable. Run with
  "/lore:verify" — wiki-scoped to match the chip count.
user_invocable: true
---

# Pending freshness verdict resolver

Walk the user through the wiki-wide freshness backlog one entry at a
time. Wiki-scoped to match the SessionStart status-line chip: when the
chip says "N pending verdict", running this command surfaces exactly
those N notes.

The primary verdict path is the LLM-emitted in-passing nudge that
fires when the agent actively cites a stale-candidate note in flow.
This slash command is the explicit fallback for the user who wants to
clear the backlog on their own schedule.

## Workflow

1. **Discover.** Call `mcp__lore__lore_pending_verdicts` (no args —
   the tool auto-resolves the active wiki from the cwd attachment).
   The response carries every picker field per entry; no further reads
   are needed.

2. **Handle empty.** If `count == 0`, say "no pending verdicts in this
   wiki" and stop. Do not invent work.

3. **Loop one picker per entry.** Entries arrive pre-sorted:
   disagreements first, then `authored_marker`, then `orphan_broken`.
   For each, show one `AskUserQuestion` with three options:
   `stale` / `confirm` / `skip`.

   **Header wording.**

   - *Disagreement* (entry has `disagreement` block):
     `[[<slug>]] — stale by <disagreement.stale_by> <disagreement.stale_at>, you confirmed <disagreement.self_confirmed_at>`
   - *Plain*:
     `[[<slug>]] — <reason>`

   In the option `description` fields, surface the cause and any prior
   personal confirm so the user has context without re-reading the
   note.

4. **On `stale`.** Show a second `AskUserQuestion` for the one-line
   reason. Offer three context-aware canned options derived from the
   entry's `reason` field (the picker UI will also expose "Other" for
   free-form entry). Then call `mcp__lore__lore_verdict` with
   `{wiki: <response.wiki>, note: entry.path, verdict: "stale",
   reason: <chosen>}`. Echo the returned freshness block so the user
   sees what landed.

5. **On `confirm`.** Call `mcp__lore__lore_verdict` with
   `{wiki: <response.wiki>, note: entry.path, verdict: "confirm"}`.
   Echo the returned freshness block.

6. **On `skip`.** No MCP call. Move to the next entry.

7. **Summarize.** End-of-loop: how many confirmed, how many stale,
   how many skipped. Mention that the SessionStart status-line chip
   will reflect the new count on the next refresh.

## Hard rules

- **Zero bash.** Everything you need is in `lore_pending_verdicts`'s
  response + per-entry `lore_verdict` calls. Do not read the catalog,
  walk the wiki, or open notes directly.
- One MCP call per verdict — do not batch (each verdict is a separate
  user choice and the per-write audit trail matters).
- Never auto-resolve a `disagreement` — always ask.
- `skip` means *make no write*; do not even open the sidecar.
- Never modify the note body. The verdict contract is frontmatter-only
  plus the per-user sidecar; both are additive.
