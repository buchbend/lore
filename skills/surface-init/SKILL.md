---
name: lore:surface-init
description: Design a wiki's full SURFACES.md set in one conversation. Holistic
  vocabulary design from one open question, optional per-surface deepening,
  writes via `lore surface commit <draft.json>`. Run with
  "/lore:surface-init <wiki>".
user_invocable: true
---

# Surface Authoring — design the full set

Guide the user through designing `$LORE_ROOT/wiki/<wiki>/SURFACES.md` from scratch. Produces a coherent vocabulary of 3-6 surfaces in one synthesis, with optional per-surface editing.

## Arguments

`/lore:surface-init <wiki>` — the positional is the wiki name.

## Step 1 — Gather context (silent)

Call `lore_surface_context(wiki=<wiki>)`.

- If `surfaces_md_exists` is `true`, warn the user: *"SURFACES.md already exists at `<path>`. Running `/lore:surface-init` will replace it (with `--force` on commit). Continue?"* — stop on `no`.

## Step 2 — Open the conversation

Ask **one** question:

> "What's this wiki for, and what kinds of things do you want to capture? A rough list or free-text description — either works."

## Step 3 — Synthesize the full set

Produce a **complete** SURFACES.md draft: 3-6 surfaces, internally consistent:

- No semantic overlap between surfaces (`decision` and `choice` don't both exist).
- Consistent naming register (all imperative-nouns, or all agent-role nouns — pick a lane).
- Consistent field schemas — `type, created, description, tags` appear in `required` for every surface unless there's a reason to drop.
- Always include a `session` surface (Curator writes session notes; the wiki needs a slot for them).
- Use `plural`, `slug_format`, `extract_prompt` only where they add real value — don't sprinkle them everywhere.

Consult `shipped_templates` for inspiration — do **not** pick one wholesale; build a set tailored to what the user described.

Build a draft-spec JSON:

```json
{
  "schema": "lore.surface.draft/1",
  "wiki": "<wiki>",
  "operation": "init",
  "schema_version": 2,
  "surfaces": [ ... ]
}
```

Call `lore_surface_validate(wiki=<wiki>, draft=<draft>)`. Revise silently on issues; report honestly if stuck.

## Step 4 — Present

Show the user:

- The rendered full SURFACES.md
- A one-line-per-surface summary ("`concept` — ideas that recur across sessions", etc.)

Ask:

> "Commit this, refine one surface, or save as draft?"

## Step 5 — Branch

**Commit:**
1. Write draft to `$TMPDIR/lore-surface-init-<timestamp>.json`.
2. Run `lore surface commit <path>` (add `--force` if SURFACES.md already exists and the user agreed in Step 1).
3. Report receipt.

**Refine one surface:**
1. Ask which surface.
2. Run a mini-loop like `/lore:surface-add` for that one surface only (open question → synthesize → validate → present just that section → accept or deepen).
3. Update the surface inside the init draft; preserve all others unchanged.
4. Return to Step 4.

**Save as draft:**
1. Write to `$LORE_ROOT/drafts/surfaces/<wiki>-init.json`.
2. Print the commit command.
3. Stop.

## Error handling, rules

Same as `/lore:surface-add`:

- Never edit SURFACES.md directly.
- Never say "Curator B"; say "Curator".
- If MCP is unreachable, stop honestly — do not fake.
- If validation keeps failing after 2 retries, show codes + ask the user.
