---
name: session
description: Write a session note to the correct Lore wiki. Delegates to
  the `lore-session-writer` subagent so main-conversation context stays
  focused on your actual work. Run with "/lore:session" at the end of a
  session.
user_invocable: true
---

# Session Note Writer (delegator)

Writes a session summary and routes it to the correct wiki. The heavy
work — reading the wiki, scanning git log, picking wikilinks, writing
the note and any extractions, committing — runs in a subagent so the
main conversation stays light.

## What you do

1. **Prepare the gist (≤300 words).** Scan the conversation for:
   - What was worked on (3–5 bullets, terse)
   - Decisions made (verbose — capture the *why*)
   - Open items (verbose — future-you needs these)
   - Any clear new concept or decision that warrants extraction
     (if nothing qualifies, say so explicitly — the subagent will not
     extract unless you flag it)
   - Repos touched (brief — the subagent re-derives from `git log`)

2. **Determine target wiki if you can.** Routing cues:
   - Content domain (project / research / personal)
   - Repo's remote URL
   - Per-wiki `CLAUDE.md` at `$LORE_ROOT/wiki/*/CLAUDE.md`
   If unsure, leave empty; the subagent will ask the user back.

3. **Dispatch the subagent.** Use the Task tool with
   `subagent_type: general-purpose` (or `lore-session-writer` if the
   custom agent is installed). Pass:

   ```
   GIST:
   <your ≤300-word summary>

   LORE_ROOT: <resolved path, e.g. $LORE_ROOT>
   CWD: <the directory Claude Code is running in>
   TARGET_WIKI: <name, or empty>
   EXTRACT: auto
   ```

4. **Report the subagent's result verbatim to the user** — do not
   re-narrate. The subagent already returns a ≤120-word summary of what
   was written.

## What you do NOT do in the main thread

- **Do not** read wiki notes yourself — the subagent does ranked
  retrieval with `lore search` in its own context.
- **Do not** run `git log`, `git commit`, or any shell commands — the
  subagent handles git in the target wiki repo.
- **Do not** write the session note or any extraction file from the
  main thread.

This separation keeps main-conversation tokens focused on your actual
work — the subagent has its own context window to do the knowledge
work in.

## If the subagent is not available

Fall back to writing the session note directly, following the same
rules as the subagent definition at
`<plugin-root>/agents/lore-session-writer.md`. Keep it terse; extract
conservatively.

## Skip trivial sessions

If the conversation was a one-shot question or a debug with no lasting
knowledge, tell the user "nothing worth recording" and stop. A session
note has value only when it helps a future session.
