# Session-note template — standard

This template documents the shape of a session note as written by
Curator A and the per-section authoring norms that the noteworthy
LLM call should respect. Per-wiki overrides go at
`<wiki>/templates/session.md` — same shape, different prose norms.

## Frontmatter contract

```yaml
schema_version: 2
type: session
created: <ISO date>
last_reviewed: <ISO date>
title: <6-8 words; content-named; slug source; no phase numbers>
description: <1-2 sentences — status-line shape, what + why in one breath>
scope: <canonical scope>
projects: [<project-note-slug>, ...]    # auto-populated from cwd repo + files_touched
plans: [<plan-slug>#s<N>, ...]          # auto-populated from Plan: trailers + body wikilinks
user: <handle>                          # team mode only
tags: []                                # free-form, omitted when empty
files_touched: [<repo-relative paths>]
transcripts: [<uuid>, ...]
source_transcripts: [...]
curator_a_run: <ISO timestamp>
```

`title` and `description` are the LLM's two named outputs that drive
human reading. `title` is short, content-named, and becomes the
filename slug (after smart word-boundary truncation). `description`
is the status-line preview SessionStart shows in the next session.
**No phase numbers** in either. Phases die; concepts live.

## Body shape (locked)

```markdown
# <title>

## Summary
<4-5 sentence narrative paragraph — what was done and why. The
rationale-rich anchor that Curator B's prefix window lands on.>

## Decisions made
- **<substance phrase>**: <rationale + outcome>

## What we worked on
- **<substance phrase>**: <detail>

## Activity                          ← parent (omit if no commits/issues)
### Commits                          ← omit if empty
- `<short-hash>` <subject> (<repo>/<branch>)
### Issues opened                    ← omit if empty
- #<N> <title> (<repo>)
### Issues closed                    ← omit if empty
- #<N> <title> (<repo>)

## Loose ends                        ← past-tense / stative grammar; never TODOs
- <X was discussed but not pursued.>
- <Y remains untested.>
```

The Activity section and its subheadings are mechanically populated
from git log + gh issue refs — no LLM authoring there.

## Section-authoring norms

### `## Summary` — the narrative

4-5 sentences. Substance, not mechanics. Says what was done and why
in continuous prose. This is what a 6-month-future reader and Curator
B's clustering pass both land on first — the rationale-rich anchor.

### `## Decisions made` — bullets with rationale

**Promote-vs-don't rule.** Include a bullet here only if the rationale
would still matter six months from now. Convention choices,
architectural calls, trade-offs with rejected alternatives — promote.
Tactical choices that won't outlast the incident — keep them in
`What we worked on`, not here. Curator B's surface extraction reads
this section to decide whether to file a `decision` surface; an
overly-eager Decisions section forces B to do extra rejection work.

**Bullet style.** Lead with a 2-5 word bold phrase that names the
*substance* of the decision (not the category). Then a colon, then
the rationale + outcome. Example:

```
- **/private/ convention** — any path under /private/ auto-gates auth;
  convention is self-documenting and repo-local.
```

NOT:

```
- **Decision made**: We decided to gate auth under /private/.
```

The first reads as substance; the second is a category label that
fights the human's scan.

### `## What we worked on` — narrative bullets

Activity narrative — what got changed and what happened. Same bold-
phrase-first style. This is where tactical choices, edits, and
discoveries land.

### `## Loose ends` — informational, never TODOs

**Critical norm.** Bullets MUST be past-tense or stative. They are
state-of-the-world observations: things discussed but not chosen,
threads that didn't go anywhere, observations the next session might
want context for.

Good:

```
- /private/ auth-gating remains undecided.
- Sphinx-with-MyST build was untested as of this session.
- Quartz MVP at ~/git/quartz-site is unused; cleanup deferred.
```

Bad (these are imperatives — they read as TODOs):

```
- Test Sphinx with MyST.
- Push the feature branch.
- Convert RST to Markdown.
```

If something is important enough to act on, file it in the configured
PM backend (gh issues / Jira / etc.) — don't put it here. Loose ends
is bookkeeping. The next-session prompt reads them as "things
discussed", not "your queue".

## Why these norms

The session note has two readers: the human re-opening the vault in
six months, and Curator B's clustering / abstraction pass. Both need
the same thing — substance up front, mechanics behind it, rationale
where it can be pulled back into a long-lived surface, observations
where they can be referenced without obligation.
