# Knowledge Wiki Conventions

Template for a wiki's root `CLAUDE.md`. Customize the taxonomy and
scope tags to fit your team.

## Structure

- `projects/` — project knowledge, organized by repo or subsystem
- `concepts/` — cross-cutting patterns that span multiple repos
- `decisions/` — ADRs: context, decision, consequences, alternatives
- `inbox/` — drop files here for processing with `/lore:inbox`

Typically mounted at `$LORE_ROOT/wiki/<name>/` in a Lore root vault.

## Hierarchical folders

`projects/`, `concepts/`, and `decisions/` support subfolders for
deep knowledge without bloated notes:

```
projects/
  <name>/
    <name>.md              ← index note: summary + links
    sub-topic.md           ← atomic deep-dive
    another-sub.md
```

**Rules for hierarchy:**
- Top-level note in a subfolder is the **index** — concise summary with
  links to deeper notes. Target < 80 lines so an LLM can scan it.
- Deep notes are small and atomic — one subsystem, one pattern.
- Every deep note `[[wikilinks]]` back to its parent index.
- Subfolders named after the parent concept/project (kebab-case).
- Flat files at the top level are fine for simple topics.
- Don't create subfolders preemptively — only when a topic grows beyond
  a single note.

## Frontmatter (required on all notes)

```yaml
---
schema_version: 2
type: project | concept | decision | paper
created: YYYY-MM-DD
description: "One-sentence summary for scanning."
tags: [topic/xxx, domain/xxx, scope/xxx]
repos: [org/name, ...]      # optional
---
```

- Do not set a `status:` field. A note in the wiki is in force.
- `description` must be filled — enables fast vault scanning.

## Tag taxonomy (customize)

Suggested prefixes (use what fits your team):

| Prefix | Purpose | Example |
|--------|---------|---------|
| `scope/` | Team/project boundary | `scope/team-name` |
| `domain/` | Repo-level subject | `domain/data-pipeline` |
| `topic/` | Technology/subject | `topic/python`, `topic/ci-cd` |
| `concern/` | Cross-cutting concern | `concern/security`, `concern/resilience` |

Notes can carry multiple scope tags when they span teams.

## Linking

- Every note must have `[[wikilinks]]` — a note with no links is a bug.
- Link the first mention of a concept in each note.
- Filenames are kebab-case of titles.
- Project notes never link to personal content.
  Personal notes can link into wiki notes freely.

## Staleness

- A note that is no longer true gets a rewrite, in a pull request.
- Delete a note that a newer note replaces, and point its links to the newer note.
- Lore flags no note by age.
- Accept some drift — the vault captures intent; code is source of
  truth for implementation detail.
