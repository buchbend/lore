# Knowledge Wiki Conventions

Template for a wiki's root `CLAUDE.md`. Customize the taxonomy and
scope tags to fit your team.

## Structure

- `projects/` — project knowledge, organized by repo or subsystem
- `concepts/` — cross-cutting patterns that span multiple repos
- `decisions/` — ADRs: context, decision, consequences, alternatives
- `sessions/` — work session logs routed to this wiki
- `inbox/` — drop files here for processing with `/lore:inbox`
- `templates/` — note templates

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
schema_version: 1
type: project | concept | decision | session | paper
created: YYYY-MM-DD
last_reviewed: YYYY-MM-DD
status: active | stable | stale | archived | proposed | accepted | superseded
description: "One-sentence summary for scanning."
tags: [topic/xxx, domain/xxx, scope/xxx]
repos: [org/name, ...]      # optional; auto-populated by /lore:session
---
```

- `last_reviewed` = date someone confirmed this note is still accurate.
- Sessions default to `status: stable` (immutable snapshots).
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
- Project notes never link to personal content (sessions/drafts).
  Personal notes can link into wiki notes freely.

## Staleness

- `last_reviewed > 90 days` + `status: active` = staleness candidate.
- The `/lore:curator` skill flags these and writes `_review.md`.
- When updating a note, always update `last_reviewed`.
- Accept some drift — the vault captures intent; code is source of
  truth for implementation detail.

## `.lore-hints.yml` (optional)

Declare which repos this wiki covers so SessionStart can scope
context to the current git repo:

```yaml
repos:
  - org/repo-a
  - org/repo-b
```

The `/lore:session` skill also auto-tags notes with `repos:` based on
commits touched during the session; over time the graph becomes
repo-aware with no manual tagging.
