---
title: Index-first context and the lab notebook
status: draft
epic: https://github.com/buchbend/lore/issues/555
repos:
  - buchbend/lore
---

# PRD 0020: Index-first context and the lab notebook

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/555).
> The epic links here; this file is not embedded in the issue body.

## Problem

A review of Lore on 2026-11-09 found five gaps. The planning of this epic showed a sixth.

- **The banner says how to behave, not what exists.** SessionStart injects 14
  directive lines, the glossary index and the neighbours. It names no ADR, no
  current PRD, no roadmap heading and no logbook. An agent learns that
  `lore_search` exists, not what the repo holds.
- **A session without the plugin gets no pointer.** A cloud session or a
  colleague without Lore sees only `AGENTS.md`. That file holds no index.
- **Some standing cost buys little.** The MCP server lists 11 tools in about
  10,000 characters of schema. Three tools serve two rarely used skills, and
  one duplicates a command the skills already call.
- **The ADR template asks for nine sections.** `Status` appears twice. Authors
  fill `Context`, `Decision` and `Consequences` with text that restates the
  `Holds` section.
- **Lab work has no home.** A calibration run or a trial with its result is
  not a decision, a defect or a how-to. Issue #417 (regime registry and
  lab-notebook brainstorm) names the gap. The logbook answers "who worked on
  what", not "what did we try, and what came out".
- **The spec waits on a branch.** `to-epic` commits the PRD on a branch.
  `build` cuts `epic/<n>` from the target branch, where the PRD does not
  exist yet. A person merges the spec by hand before each build.

## Solution

- **Index.** One generator, `lore index`, prints a short index of the repo:
  the glossary terms, the ADR titles, the current PRDs, the roadmap headings,
  the neighbours and the last lab entries. Each line group ends with the
  command that pulls the detail. SessionStart injects the index. `lore index
  --write` keeps the same text in a marked block of `AGENTS.md`.
- **Filing rules on demand.** Two directive lines stay in the banner: the
  filing trigger and the ADR reading rule. The routing detail moves to `lore
  style show filing`. The trigger line names that command.
- **Slimmer MCP surface.** Seven tools remain. The freshness-verdict tools,
  the inbox tool and the tier tool become commands that their skills call.
- **Lighter ADR template.** An ADR needs its header, `Holds`, `Revisit if` and
  `Alternatives considered`. One free `Background` section replaces
  `Context`, `Decision` and `Consequences`. The second `Status` section goes.
- **Lab notebook.** A new skill, `experiment`, runs one experiment and writes
  one lab entry at its end. `lore lab add` checks the entry, stamps the
  provenance and delivers the entry through the scope's sinks. ADR 0019 (the
  lab notebook) records the decision.
- **Spec branch.** `to-epic` names the branch of the PRD in the epic body.
  `build` in `epic` mode takes that branch into `epic/<n>` when the target
  branch lacks the PRD. The start of the build counts as the approval of the
  spec.

## Implementation decisions

### Index

- `lore index` reads `CONTEXT.md`, `docs/adr/`, `docs/prd/`, `ROADMAP.md`, the
  system map and the lab notebook. It makes no model call and no network call.
- The index lists an ADR as number and title. It lists a PRD only while its
  status is not `shipped`. It lists the three newest lab entries by date and
  question.
- A group without content prints no line. A repo with none of the inputs gets
  an empty index.
- The index has a budget of 2,000 characters. Over budget, each group keeps
  its newest lines and ends with a count of the lines left out.
- SessionStart places the index after the neighbours, where the glossary
  index sits now. The glossary index becomes a group of the index.
- `lore index --write` replaces the text between two markers in `AGENTS.md`.
  `lore index --check` exits 1 when the block differs from the generated
  text. `lore setup scope --scaffold-workflow` writes the first block.
- The block changes when an ADR, a PRD, a roadmap heading or a term changes.
  Lab entries stay out of the committed block, so a lab entry does not make
  the block stale.

### Filing rules

- `lore style show filing` prints the routing lines that the directive holds
  now. The directive template keeps the filing trigger, the session-end
  listing and the reading rule.
- The `file-issue` skill reads `lore style show filing` before it files.

### MCP surface

- Tools kept: `lore_search`, `lore_read`, `lore_drill`, `lore_context_pack`,
  `lore_repo_docs_list`, `lore_repo_docs_fetch`, `lore_codemap`.
- Tools removed: `lore_pending_verdicts`, `lore_verdict`,
  `lore_inbox_classify`, `lore_tier_resolve`. Each gets a command with JSON
  output where none exists. The `verify` and `inbox` skills call the commands.
- The serialised tool schema stays under 6,000 characters.

### ADR template

- `lore wiki lint adr` requires `Holds` and accepts both section sets. It
  applies to ADRs from 0014 on, as before.
- ADR 0015 (decision records state their strength) gets a dated amendment.
  ADR 0019 already uses the new sections.
- Existing ADRs stay as they are.

### Spec branch

- `to-epic` writes one line into the epic body: `**Spec branch:** <name>`.
  It pushes the branch before it files the epic.
- `lore workflow spec-branch` reads an epic body and the repo. It prints
  JSON: the branch, and whether the target branch already holds the PRD.
- `build` in `epic` mode runs the command in its Map step. When the target
  branch lacks the PRD, `build` merges the spec branch into `epic/<n>` right
  after it creates `epic/<n>`. The spec then reaches the target branch with
  the epic PR.
- When the target branch holds the PRD, `build` does nothing with the spec
  branch.
- When the epic body names no spec branch and the target branch lacks the
  PRD, `build` stops and names the missing PRD.
- A direct merge into the target branch stays out: a protected branch
  rejects the push, and a second PR adds one more wait for CI.

### Lab notebook

- A lab entry has five labelled parts: `Question`, `Setup`, `Result`,
  `Interpretation` and `Next`. `lore lab add` rejects an entry without the
  five labels or with more than 400 words.
- Lore writes the stamp, not the agent: date, author, repo, commit, a dirty
  flag, branch and host. `--cmd` and `--data` add the command line and the
  data paths. Lore adds a SHA-256 for each data path that is a readable file.
- `--thread <slug>` groups entries that follow one idea. `--status
  open|concluded|dead-end` states where the idea stands. The default is
  `open`.
- The `file` sink writes one file per entry under `docs/lab/YYYY-MM/`. The
  `command` sink works as it does for the logbook. A scope lists lab sinks in
  `lab.sinks`; without the key, the list is `[file]`.
- An undelivered entry waits in the local ledger as a `[lab]` line. The
  hooks retry the line with the logbook lines.
- `lore lab show [--thread <slug>]` prints entries, newest first.
- The `experiment` skill has three steps. It writes the question and the
  expected result before the run. It runs the experiment. It writes the entry
  and commits the file on the current branch. A dead end gets an entry with
  status `dead-end`, not a closed issue.
- The logbook module and the lab module share the sink delivery code.

## Testing decisions

- Test behaviour through the command line and the hook output, as
  `tests/test_log_cmd.py` and the SessionStart tests do.
- Index: a fixture repo with two ADRs, one shipped PRD and one draft PRD
  gives an index that names both ADRs and only the draft PRD. A second test
  covers the budget. A third covers `--check` on a stale block.
- Filing rules: the banner holds the trigger and the reading rule and not
  the routing lines.
- MCP: one test asserts the tool names and the schema budget.
- ADR lint: an ADR with the new sections passes; an ADR without `Holds`
  fails.
- Spec branch: a fixture repo with the PRD on a side branch gives
  `needs_merge: true`; with the PRD on the target branch it gives `false`.
- Lab: `lore lab add` rejects a missing label. The stamp holds the commit of
  a fixture repo. A dirty tree sets the dirty flag. The file sink writes one
  file per entry.

## Out of scope

- The regime registry of issue #417.
- Distilling lab entries into wiki topic notes.
- Capture of plots or binary results. An entry links to them by path.
- Shorter PRDs, the lint-first use of the writing rules and the ceremony of
  `build`. Measure them with `lore stats` first.
- A rewrite of existing ADRs.
