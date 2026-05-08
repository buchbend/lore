## Directives
- **Vault first.** Unfamiliar project term, concept, decision, or wikilink? Call `lore_search` (MCP) before asking the user. Asking about a wikilinked term without searching first is a bug.

### Lore freshness — in-passing nudge

When you cite or rely on a note that Lore returned with `freshness.status: "stale-candidate"`, end your turn with a one-line check that quotes the specific claim you used:

`(› [[<note>]] said "<claim>" — <reason>; still current?)`

Rules:
- Once per note per session. Track via session-local memory; do not retry.
- Silence semantics: if the user types past the nudge without responding, do nothing. Never auto-write a verdict.
- If the user replies with a verdict ("yes still good" / "no, stale because X" / "split it — first part is stale"), call the `lore_verdict` MCP tool with the appropriate arguments before continuing your substantive answer.
- On a "split it" verdict, offer to edit the note: move the fresh content out, mark the rest stale via `lore_verdict`. The user confirms before any write.

### Lore freshness — dynamic escalation

If a retrieved note returns `freshness.status: "confirmed"` but you observe a *concrete claim-vs-claim contradiction* — claim A in this note vs claim B in another retrieved hit, or vs a fact the user just stated this session — treat the note as `stale-candidate` for this turn and emit the in-passing nudge.

Do NOT escalate on:
- Silence (the note doesn't mention X).
- Topic mismatch (the note discusses an adjacent thing).
- Vibe-level disagreement without two stated claims to compare.

### Lore freshness — disagreement nudge

When the freshness block carries `disagreement` (someone marked the note stale, someone else confirmed it after — including the same user changing their own mind), use the disagreement-flavored nudge instead of the regular one:

`(› [[<note>]] marked stale by <stale_by> on <stale_at> (<reason>); you confirmed on <self_confirmed_at> — clarify?)`

Never auto-resolve. The system never auto-clears a stale or auto-overrides a confirm; offer the user a fresh `lore_verdict` call once they decide which side wins.
