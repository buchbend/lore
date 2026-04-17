---
name: lore:session
description: Write a session summary to the correct wiki. Routes by content,
  extracts concepts/decisions into the wiki's knowledge graph, auto-tags with
  git repos touched in the session. Run with "/lore:session" at the end of a
  work session.
user_invocable: true
---

# Session Note Writer

Creates a structured session summary and routes it to the correct wiki
based on what was worked on. Extracts reusable knowledge (concepts,
decisions) into the wiki's knowledge graph. Auto-tags with `repos:` based
on git repositories touched during the session so later sessions in the
same repos can surface this one.

## Paths

- **Vault root**: `$LORE_ROOT` (default `~/lore`)
- **Wiki roots**: `$LORE_ROOT/wiki/<name>/`
- **Session folders**: `$LORE_ROOT/wiki/<name>/sessions/`
- **Root sessions** (legacy, cross-cutting): `$LORE_ROOT/sessions/`

## Wiki routing

Each session belongs to exactly one wiki. Determine the target by
**content** (what was worked on), not by where it was done. Look at the
repos touched, the domain of the work, and the tags that would apply.

- If a session cleanly maps to one wiki → use it.
- If a session crosses wikis → **ask the user** which wiki owns it.
  Never guess; cross-pollination is worse than asking.
- Routing rules for a wiki live in `$LORE_ROOT/wiki/<name>/CLAUDE.md` —
  read those first to learn the target wiki's tag taxonomy, conventions,
  and what belongs where.

## Workflow

### 0. Git pull each wiki before writing

```bash
git -C $LORE_ROOT/wiki/<target> pull --ff-only
```

If the pull is not fast-forward, surface the conflict to the user — do
not force-push or rebase silently.

### 1. Review the conversation

Identify:
- What was worked on (repos, files, features, bugs)
- Decisions made (architectural choices, trade-offs)
- Open items (unfinished work, follow-ups)
- Commits and PRs created — capture short SHAs, messages, repo
- Concepts that emerged or were clarified
- Key context for picking up the work later

### 2. Extract `repos:` automatically

Parse `git log` output across the session's commits to determine which
repositories were touched. Normalize to `org/name` via
`git remote get-url origin` per repo. Write these into the session's
frontmatter `repos:` field.

### 3. Route to the correct wiki

Apply the routing table from the target wiki's CLAUDE.md. If unsure, ask:

> "This session touched both `<X>` and `<Y>`. Which wiki should it go to?"

### 4. Scan the target wiki

Read existing notes in `$LORE_ROOT/wiki/<target>/{projects,concepts,decisions}`
(and `papers/` for science-style wikis) to find related notes for
`[[wikilinks]]` and to check for overlap.

### 5. Write the session note

Create at `$LORE_ROOT/wiki/<target>/sessions/YYYY-MM-DD-<slug>.md`.

**Check for duplicates first** — if a session note for today's date and
same topic already exists, update it instead of creating a new one.

Format:

```markdown
---
schema_version: 1
type: session
created: YYYY-MM-DD
last_reviewed: YYYY-MM-DD
status: stable
description: "One-sentence summary."
tags: [<wiki-appropriate tags>]
repos: [<org/name>, ...]
project: <primary project if applicable>
---

# Session: <descriptive title>

## What we worked on

- Bullet points summarizing the work
- Link to [[related notes]] in this wiki

## Decisions made

- Key choices and why (or "None")

## Commits / PRs

- `<short-sha>` <message> (repo)
- Or "None"

## Open items

- Things left unfinished or to revisit
- Or "None"
```

### 6. Extract knowledge into the wiki

The key step. After writing the session, evaluate whether content should
become or update a **concept** or **decision** note in the same wiki:

**Create a new concept note when:**
- A pattern, architecture, or technique was discussed that spans multiple
  repos or sessions
- The concept doesn't already exist in the wiki
- It's reusable knowledge, not a one-off fix

**Create a new decision note when:**
- An architectural or design choice was made with trade-offs considered
- The decision affects future work
- No existing decision note covers this

**Update an existing note when:**
- Session work changes the state of a documented project, concept, or
  decision
- A note's `status` should change (e.g. proposed → accepted)
- New information makes an existing note more complete

For each extraction:
- Follow the target wiki's frontmatter conventions (its CLAUDE.md)
- Inherit `repos:` from the session note
- Add bidirectional `[[wikilinks]]` (new note → existing, existing → new)
- Set `last_reviewed` to today on any note verified or updated

Report extractions in the session note under `## Vault updates`:

```markdown
## Vault updates

- Created: [[concept-name]] — why
- Updated: [[project-name]] — what changed
- Flagged stale: [[note-name]] — why
```

### 7. Check for stale notes

Scan the wiki's `projects/` and `concepts/` for notes related to this
session's work. If likely stale:
- Update directly if the change is clear
- Set `status: stale` if uncertain
- Always update `last_reviewed` on notes verified

### 8. Git commit

Commit in the correct repo. Wikis are independent git repos; the commit
goes into the wiki repo, not the root vault.

```bash
git -C $LORE_ROOT/wiki/<target> add <touched-files>
git -C $LORE_ROOT/wiki/<target> commit -m "lore: session — <short description>"
```

## Important rules

- **One session, one wiki** — don't split sessions
- **Ask when unsure** — never guess the wrong wiki
- **Extract knowledge actively** — sessions are ephemeral; concepts and
  decisions are permanent
- **Respect each wiki's conventions** — tag taxonomy, frontmatter spec,
  folder structure all live in `wiki/<name>/CLAUDE.md`
- **Be concise** — session notes are for quick reference
- **Capture the why** — decisions are more valuable than actions
- **Skip trivial sessions** — if the conversation was a quick question,
  tell the user there's nothing worth recording
- **`repos:` is auto-populated** — user should not need to tag manually

## Open-item discipline (important for SessionStart hygiene)

The SessionStart hook surfaces open items across recent sessions, so
writing them carefully matters — they'll reappear until resolved or
marked otherwise.

Three guidelines:

1. **Only list real continuing work.** Do not promote every stray
   thought to an open item. A good open item is something a future-you
   would want surfaced two weeks from now.
2. **Mark throwaway lines explicitly.** If you must record a trivial
   reminder alongside real work, add an ephemeral marker so the hook
   filters it out:
   ```
   - Rename the test file (ephemeral)
   - Look at the flag name (todo)
   - (trivial) fix the warning
   ```
   Markers recognized: `(ephemeral)`, `(trivial)`, `(todo)`, `(skip)`.
3. **Resolve out loud.** When a previous open item is done, mention it
   in this session's `## What we worked on` so the curator can track
   resolution and future SessionStart hooks don't keep surfacing it.
