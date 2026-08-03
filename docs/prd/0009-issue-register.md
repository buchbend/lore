---
title: "The issue register: per-team prose style for agent-written issues"
status: draft
epic: https://github.com/buchbend/lore/issues/310
repos:
  - buchbend/lore
---

# PRD 0009: The issue register: per-team prose style for agent-written issues

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/310).
> The epic links here; this file is not embedded in the issue body.

## Problem

Agent-written issues read polished and cost readers real effort. An
international team converges on a shared subset of English. Agents pull
toward a wider vocabulary, denser sentences, and context that existed only
in the session that wrote the issue.

A review of four recent agent-written issues (ccatobs/ops-db-api#265,
ccatobs/ops-db-api#269 to ccatobs/ops-db-api#271) showed the concrete
failures: no shared section structure, no acceptance criteria, sentences
over 30 words, and epic context assumed instead of stated. Vocabulary was
not the failure: the banned-word list of issue
[#303](https://github.com/buchbend/lore/issues/303) had zero hits.

Teams differ. A style fixed in one skill serves one team. Lore serves
several.

## Solution

Lore ships a default issue register: the #303 draft (ASD-STE100-derived
prose rules, EARS acceptance criteria, a fixed section skeleton) with three
ratified edits. A team overrides the register by placing one file in its
wiki: `<wiki>/style/issue-register.md`. Resolution is whole-file: the wiki
file wins, else the default. There is no merge and no per-repo layer.

Agents reach the register through one command, `lore style show
issue-register`. The SessionStart banner carries a one-line directive:
resolve the register before writing an issue or PR body. All lore-workflow
filing routes through one in-context `file-issue` skill: resolve the
register, draft in its skeleton, lint with Vale when Vale is on PATH, then
post. ADR 0005 records the decisions.

## Implementation decisions

- The register is a prose document, not config. Resolution is whole-file
  per wiki. See
  [ADR 0005](../adr/0005-issue-register-whole-file-override-generation-time-lint.md).
- The default register ships as lore package data, next to the existing
  packaged templates. The CLI is the only resolver; the plugin carries no
  copy of the file.
- The register text is #303 plus three edits: a "Batch issues" section
  (a change is one required-behaviour statement with its own criteria; a
  batch is several changes, one Context, one PR, no ordering
  dependencies), rule 14 accepts code-flavored provenance (file path and
  line, command output, test names), and the checkability claim names
  only rules 3 and 6 as linted, 9 and 12 as heuristic, 4 and 10 as
  review-only.
- New CLI group `lore style`: `show <name>` prints the resolved register;
  `vale-config` prints the resolved Vale config path
  (`<wiki>/style/vale/vale.ini` wins, else the packaged default).
- The default Vale style covers rule 3 (banned words), rule 6 (sentence
  length), and regex heuristics for rules 9 and 12. Vale is PATH-detected.
  Absence degrades to instruction-only enforcement and never blocks.
- The SessionStart banner gains one directive line pointing at
  `lore style show issue-register`. No CLAUDE.md block is written; the
  register text keeps the #303 paste-block section for consumers without
  Lore.
- One in-context skill, `lore-workflow:file-issue`, owns the funnel:
  resolve, draft (single change, batch, caller template, or PR-body
  mode), Vale loop, post. It never spawns a subagent. to-epic, seed-epic,
  orchestrate-epic follow-ups, and implement-issue call it instead of
  filing inline.
- to-epic's sub-issue template becomes an epic-linkage header plus the
  register skeleton. Machine-readable formats stay exempt: the roadmap
  table, board comments, and reviewer verdicts.
- The house-style section in `docs/conventions.md` becomes a pointer to
  the register. `CONTEXT.md` gains the terms register, change, and batch
  issue.

## Testing decisions

Test external behaviour of the CLI, not skill prose. Resolution tests
cover the default path, the wiki override, and an unknown style name.
Register-content tests assert the three edits are present. Vale tests run
the real binary against a fixture that contains one banned word and one
30-word sentence, and skip when Vale is absent. A doctor test asserts the
vale line. Prior art: the existing CLI tests and the `templates_dir()`
package-data tests. Template conformance is tested through
`lore workflow validate-roadmap` on an epic body composed from the new
sub-issue template.

## Out of scope

- CI lint (commit or PR hooks). Lint runs at generation time only.
- Mechanical checks for glossary rules 1 and 2.
- A `lore style block` print command. Deferred until a consumer without
  Lore exists; the paste-block section stays in the register text.
- Applying the register to session notes, commit messages, code comments,
  or chat.
- A per-repo override layer.
- Proactive capture judgment (what is worth filing). The funnel owns how
  to write and file; deciding what stays with the caller.
