---
title: The roadmap
status: draft
epic: https://github.com/buchbend/lore/issues/510
repos:
  - buchbend/lore
---

# PRD 0018: The roadmap

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/510).
> The epic links here; this file is not embedded in the issue body.
> Decisions: ADR [0018](../adr/0018-lore-and-project-manage-meet-through-generic-contracts.md)
> (Lore and project-manage meet only through generic contracts), ADR
> [0014](../adr/0014-one-home-per-fact.md) (one home per fact, amended with the
> roadmap row). Terms: Roadmap, Roadmap item, Milestone and Build order in
> `CONTEXT.md`.

## Problem

- ADR 0014 (one home per fact) has no home for the question "Where is the
  code heading, and in what order?". The answer lives in the owner's head.
- Where the answer is written down, it lands in an instruction file. The
  `CLAUDE.md` of chai-software holds a `# GOAL` section and a phase status
  that goes stale.
- Free-form roadmap files mix direction with architecture and status. Examples:
  `reduction_pipeline/ROADMAP.md` in chai-software (300 lines) and
  `docs/architecture/pki-security-roadmap.md` in system-integration (929 lines).
- A session that designs or builds a feature cannot check the plan against
  the direction. Decisions made on the spot miss the larger order.
- Lore already uses the word "roadmap" for the feature table of an epic
  tracker issue. The word cannot carry the new meaning while it keeps the old.

## Solution

- A roadmap is a `ROADMAP.md` file in the repo. A component directory of a
  monorepo can hold its own file. The file at the repo root is the repo's
  roadmap.
- The file says where the code is heading, then lists one bullet paragraph per
  roadmap item in the intended order. A section `## Not placed yet` holds
  ideas without a place in the order.
- A roadmap item can link down to its PRD, epic or issue. It can name a
  milestone by ref, such as `pm:CCAT-K7QX2M`. The milestone holds the date.
- A person files an idea with one command: `lore roadmap add "Title"
  ["paragraph"]`. The item lands under "Not placed yet" on the current branch.
  The next PR carries it.
- The workflow skills read and keep the roadmap:
  - `orient` reads the roadmap and takes a roadmap item as its seed.
  - `grilling` asks which item a plan serves.
  - `to-epic` adds the PRD and epic links to that item.
  - `build` removes the shipped item at its finish point.
  - An agent adds a new feature idea under "Not placed yet" through its PR.
- A roadmap across repos is a wiki topic note, per the ADR 0014 row "What
  spans several repos?". For CCAT, this is a `roadmap` note in the `ccat` wiki.
- The epic feature table is renamed to "build order", so "roadmap" carries one
  meaning.

## Implementation decisions

### The roadmap file

The shape of `ROADMAP.md`:

```markdown
# Roadmap

One paragraph: where the code is heading.

- **Item title.** What and why, in one paragraph.
  Links: PRD, epic, issue · milestone pm:<id>

## Not placed yet

- **Item title.** An idea without a place in the order.
```

- The order of the top list is the intended order.
- The file holds no dates, quarters or seasons. Dates stay in milestones.
- The file names no owners. The PRD or the milestone names the owner.
- An item with an issue links to the issue and does not restate its text.
- A dropped item moves down to "Not placed yet". A shipped item leaves the
  file. `CHANGELOG.md` and the shipped PRD keep the record.
- Links go down, from the item to the PRD, epic or issue. A PRD gets no field
  that points up to an item.

### `lore roadmap add`

- The command appends one item under `## Not placed yet` in the nearest
  `ROADMAP.md` above the working directory, inside the repo. Without one, it
  uses the file at the repo root.
- A missing file gets the skeleton above. A missing section gets added at the
  end of the file.
- The command writes the working tree only. It does not commit, push or open
  a pull request.

### Skills

- `orient` reads the roadmap of the repo, and the roadmap of the component
  when the work sits in a component directory.
- A milestone ref in an item is read with `lore_read` when a source serves the
  ref. Without a source, the ref stays plain text.
- The filing rule separates an idea from an issue. A feature idea outside the
  run's scope goes to the roadmap. A defect or a concrete change goes to an
  issue.

### The build-order rename

- The epic table heading becomes `## Build order`.
- The command `lore workflow validate-build-order` checks the table.
  `validate-roadmap` stays as a hidden alias, because the CLI and an older
  plugin cache can differ in version.
- The validator finds the first table in the text and ignores its heading. Open
  epics with a `## Roadmap` heading keep their checks.
- Skill text, the READMEs, `docs/conventions.md`, Python modules and tests use
  the build-order names.

### Project-manage

- Lore holds no project-manage code (ADR 0018). project-manage ships a
  `pm source` adapter that speaks Lore's `command` source contract. That work
  is a project-manage issue, outside this epic.

## Testing decisions

- `lore roadmap add`: tests drive the command against a temporary repo. They
  cover a missing file, a missing section, a component file and an append to
  an existing list. Prior art: the `lore log add` tests in
  `tests/test_logbook.py`.
- The rename: the existing validator tests move to the new names. One test
  calls the hidden alias. One test feeds an epic body with a `## Roadmap`
  heading.
- ADR 0018: a test fails when a module under `lib/` imports a project-manage
  package. The first Holds line of ADR 0018 becomes an invariant that names
  the test.
- Skills: the structural skill tests check that `orient`, `grilling`,
  `to-epic` and `build` name the roadmap step. Prior art:
  `tests/test_workflow_plugin_structural.py`.

## Out of scope

- The `pm source` adapter (a project-manage issue).
- The `ccat` wiki roadmap note (a wiki pull request with the owner's content).
- The migration of existing roadmap files in chai-software and
  system-integration (one issue on each repo).
- A SessionStart banner line for the roadmap. The owner rejected the line,
  because the next item is often hard to time. Skills keep the roadmap
  visible by use.
- The agent task loop from project-manage boards (a later PRD).
- Deferred, each with the condition that brings it back:
  - A standing roadmap pull request, if filing on the current branch causes
    friction.
  - `lore roadmap show --scope`, when three repos hold a `ROADMAP.md`.
  - A roadmap mode of `triage`, when a roadmap first goes stale.
  - A roadmap key in the context pack, when a skill misses the file.
  - A display of roadmaps in project-manage, if scientists need the direction
    and not only the dates.

## Signal to watch

The share of new PRDs that start from a roadmap item. A share below one half
after two months means the file is decoration.
