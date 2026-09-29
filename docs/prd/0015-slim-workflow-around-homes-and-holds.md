---
title: Slim the workflow around one home per fact and decision records that state their strength
status: accepted
epic: https://github.com/buchbend/lore/issues/443
repos:
  - buchbend/lore
---

# PRD 0015: Slim the workflow around one home per fact and decision records that state their strength

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/443).
> Decisions recorded in ADR [0014](../adr/0014-one-home-per-fact.md)
> (one home per fact) and ADR
> [0015](../adr/0015-decision-records-state-their-strength.md) (decision
> records state their strength).
> Design input: review and grilling session 2026-09-29, branch
> `ccr-2ff80ed2-mwwmlr`.
> Depends on PRD [0014](0014-retire-flags-attach-facts-to-artifacts.md),
> epic issue 419. PRD 0015 starts after issue 419 merges.

## Problem

The owner wants four things from Lore:

- Information goes to one place.
- The team can later see why work was done.
- The docs stay in order.
- Code ships fast with a reliable handover.

The review on 2026-09-29 found gaps on each.

- `lore-workflow` ships 17 skills. Five of them build code:
  `orchestrate-epic`, `quick-orchestrate`, `super-orchestrate`,
  `implement-issue` and `quick-feedback-loop`. Each writes reasons to a
  different place.
- Most tokens go to the workflow, not to Lore core. The SessionStart hook
  injected 251 bytes in this repo (`lore hook session-start --plain
  --probe`, 2026-09-29). `orchestrate-epic/SKILL.md` alone is 17,099 bytes
  and loads on every run.
- `orchestrate-epic` reviews one feature up to three times: the per-PR
  crosscheck, the whole-epic review and the docs-versus-behaviour check.
  The per-PR reviewer runs at the `strong` tier for every PR, whatever the
  size of the diff.
- The reviewer's checklist asks a model to confirm CI status and ruff
  output. `gh pr checks` answers both without a model.
- Only `file-issue` and the `quick-feedback-loop` wrap-up call `lore style
  show writing-rules` (grep over `lore-workflow/skills`, 2026-09-29). PR
  bodies, ADRs, PRDs and docs skip the writing rules.
- A report from another session on 2026-09-29 found agents obeying old
  ADRs as law. The records gave the decision and the mechanism of the day
  equal weight. See ADR 0015.
- Each behaviour merge to `main` needs a second PR for the version bump.
  `CHANGELOG.md` lists 108 releases since 2026-04-17.
- Nobody measures tokens per skill phase. The next trim has no data.

## Solution

Three changes, one per owner goal.

**One home per fact** (ADR 0014). Each kind of fact has one home. Lore
checks the outcome, not the prose. Every build run keeps a ledger of ADR
candidates, term candidates and left-on-the-table items. The ledger check
blocks the finish point until each line is approved, dropped or filed. A PR
body or a PRD takes whatever shape its task needs.

**Decision records state their strength** (ADR 0015). An ADR carries
`Holds` (invariant, default, incidental), `Revisit if` and `Amendments`. A
test marks each invariant. Agents read ADRs and PRDs as context. A shipped
PRD points at its ADRs and drops out of injection.

**One `build` skill with three modes.** `loop` keeps the quick feedback
loop: sloppy rounds, local merges, a wrap-up that collects what was left on
the table. `issue` takes one issue to one PR. `epic` runs a roadmap. The
review depth follows a deterministic risk level. Each mode writes resume
lines, so `/clear` and compaction lose nothing.

The skill set shrinks from 17 to 9:

| Skill after PRD 0015 | Replaces |
|---|---|
| `orient` | `orient`, `brief` |
| `grilling` | `grilling`, `domain-modeling` |
| `to-epic` | `to-epic` |
| `build` | `orchestrate-epic`, `quick-orchestrate`, `super-orchestrate`, `implement-issue`, `quick-feedback-loop` |
| `tdd` | `tdd` |
| `debug` | `debug` |
| `document` | `document-epic`, `consolidate-docs` |
| `handover` | `seed-epic` |
| `file-issue` | `file-issue` |

`ccat-workflow-init` leaves. `lore attach --scaffold-workflow` already does
the work.

## Implementation decisions

### Decision records

- **ADR template** — `ADR-FORMAT.md` gains `Holds`, `Revisit if` and
  `Amendments`, in the form of ADR 0015. The template moves with
  `domain-modeling` into `grilling`.
- **`lore lint adr`** — a new CLI verb. It parses the `Holds` section of
  each ADR numbered 0014 or higher. It fails when a section is missing. It
  fails when an invariant names a test file or a test function that does
  not exist. It skips ADRs 0001 to 0013. CI runs the verb.
- **Reading rule** — the SessionStart directive cluster gains the ADR 0015
  reading rule, one paragraph. `lore attach --scaffold-workflow` writes the
  same paragraph into `AGENTS.md`.
- **Absolutes** — a new writing rule and a Vale rule
  `WritingRules/Absolutes.yml`. The rule flags `always`, `never`, `no
  exceptions`, `fixed` and `must` in `docs/adr/**` and `docs/prd/**`. The
  rule skips code spans and lines that start with `- **Invariant**`.
- **Shipped PRD** — the `document` pass at the epic tail sets the PRD's
  `status` to `shipped` and adds one line under the title that names the
  current ADRs. The context pack and `lore_search` rank a shipped PRD below
  ADRs. SessionStart does not inject a shipped PRD.

### Writing rules everywhere

- Every skill that writes team-facing text runs `lore style show
  writing-rules` before writing: `grilling`, `to-epic`, `build`,
  `document`, `handover` and `file-issue`.
- CI runs Vale with the Lore config over `docs/**/*.md` and fails on an
  error.
- A CI job lints the PR body with the same config and posts warnings only.
- The `Scope` line of `writing-rules.md` names PR bodies, ADRs, PRDs, docs
  and the handover section.

### Ledger and breakpoints

- **Ledger location** — `loop` and `issue` keep the ledger at `$(git
  rev-parse --git-dir)/lore-ledger.md` in the worktree. `epic` keeps the
  ledger in a `## Ledger` section of the board comment, below the table.
  `parse-board` gains a reader for that section.
- **Ledger lines** — four kinds: `adr`, `term`, `left` and `resume`. Each
  non-resume line carries an outcome field: `open`, `approved`, `dropped`
  or `filed <owner/repo#n>`.
- **`lore workflow ledger-check`** — reads the ledger of the current mode.
  It exits non-zero while a non-resume line is `open`. Each finish point
  runs the check.
- **Resume lines** — after each merged round or feature, the agent writes
  one resume line: the state and the next ask. The agent then tells the
  user that `/clear` is safe.
- **Hooks** — `lore hook pre-compact` names the ledger path in its
  `systemMessage`. `lore hook session-start` finds an open ledger in the
  current worktree and offers to resume from its last resume line.

### Build

- **Mode choice** — `loop` when the user reacts to a running feature, as
  `quick-feedback-loop` defines today. `issue` for one issue. `epic` for a
  tracker issue with a roadmap. The existing effort-band rule picks one
  teammate or parallel teammates inside `epic`.
- **`loop` wrap-up PR** — rounds merge locally for speed. Work reaches the
  remote only through one wrap-up PR from `loop/<slug>`. The PR body is the
  handover section of the loop.
- **Deterministic checks leave the reviewer** — the orchestrator reads CI
  status and ruff output from `gh pr checks`. The reviewer judges only
  correctness (`code-review`), acceptance-criteria coverage and scope.
- **`lore workflow risk <pr>`** — prints `low` or `high` with the reasons.
  The level is `high` when one of these holds:
  - more than 400 changed lines, or more than 10 changed files;
  - a changed file sits in the top decile of codemap fan-in;
  - the diff adds a migration, a schema file, a public API route or a
    config schema key;
  - the diff touches a path the repo config marks as sensitive (auth,
    permissions, secrets).
  Thresholds and paths live in the root config with these defaults.
- **Review by risk level** — `low`: `code-review low` by a `mid`-tier
  reviewer. `high`: `code-review medium` by a `strong`-tier reviewer. An
  agent raises the level and does not lower it.
- **Whole-epic review** — runs for epics with three or more features. The
  same reviewer checks the docs commit, so the docs check is no separate
  round.
- **Board** — keeps its table and the `## Ledger` section. The notes
  section holds blockers and escalations only.
- **Handover section** — `build` writes `## Handover` into the epic issue
  body at the finish point: what shipped with the merge SHA, decisions with
  ADR links, deviations from the PRD, follow-up issues and known limits.
  `handover` (renamed from `seed-epic`) writes the same section for work
  that stops before its finish point.
- **Skill size** — `build/SKILL.md` holds the core loop per mode. The
  verdict format, the epic tail and cleanup live in sibling files that the
  skill loads when a run reaches them.
- **Tier floor** — `super-orchestrate` leaves. A user who runs several
  epics starts one `build` run per epic.

### Release

- `tools/release.py --in-branch` adds the bump commit to the current branch
  instead of opening a release PR. The shipping PR carries the bump as its
  last commit.
- `CLAUDE.md` § Releasing names the in-branch form. The version-sync guard
  stays.
- Two open PRs that both bump conflict on `CHANGELOG.md`. The second PR
  merges `main` and runs `--in-branch` again.

### Token trace

- `lore trace tokens <session>` reads the usage fields of each assistant
  message in the transcript copy. It sums input, output and cache tokens
  per skill phase. A phase starts at each `Skill` tool call and at each
  subagent spawn.
- The command prints a table and supports `--json`.

### Docs order

- `docs/conventions.md` links to the ADR 0014 table and drops its own
  artifact-homes table.
- The first `document` run tidies Lore's own repo. `brainstorms/`,
  `docs/model-tiers.md` and `docs/session-note-teardown-sweep.md` sit
  outside the Diátaxis folders today.

## Roadmap input for `to-epic`

`to-epic` keeps the fewest slices that earn a split. Three slices run in
parallel. The skill slice waits for the CLI verbs the skills call.

| # | Feature | Type | Blocked by |
|---|---|---|---|
| 1 | Decision records: ADR template, `lore lint adr`, reading rule, absolutes rule, Vale in CI, shipped-PRD ranking | AFK | — |
| 2 | Ledger, `ledger-check`, breakpoint hooks and `lore workflow risk` | AFK | — |
| 3 | `tools/release.py --in-branch` and `lore trace tokens` | AFK | — |
| 4 | `build` skill with three modes, skill merges, handover section, writing rules in every text skill | HITL | 1, 2 |

The epic tail runs the docs pass. The pass points `conventions.md` at ADR
0014 and tidies Lore's own docs. Feature 4 is HITL: the owner reviews the
merged skill text in the epic PR before merge.

## Testing decisions

- `lore lint adr`: fixture ADRs with a missing `Holds`, an invariant with a
  missing test, an invariant with an existing test, and ADR 0013. Expect
  fail, fail, pass, skipped.
- Absolutes rule: Vale over fixture text with the words in prose, in a code
  span and on an invariant line. Expect one alert per prose hit only.
- Reading rule: SessionStart output in an attached fixture repo contains the
  directive. The scaffold writes the same text into `AGENTS.md`.
- Ledger check: fixture ledgers for each mode, with and without `open`
  lines. Expect exit codes 1 and 0.
- Breakpoint hooks: the pre-compact message names the ledger path; the
  SessionStart output offers a resume when a ledger with a resume line
  exists.
- Risk level: stubbed diffs over each threshold and one under all of them.
- Skill prose: a grep test asserts that each text-writing skill runs `lore
  style show writing-rules`, and that `build` runs `ledger-check` at each
  finish point.
- Release: `--in-branch` on a fixture repo adds one commit that bumps the
  three version files and opens no PR.
- Token trace: a fixture transcript with two skill calls and one subagent.
  Expect three phases with summed usage.

## Acceptance criteria

- When `lore lint adr` runs, Lore shall fail on an ADR numbered 0014 or
  higher without a `Holds` section.
- If an invariant line names a test that does not exist, then `lore lint
  adr` shall fail and name the line.
- When `lore hook session-start` runs in an attached repo, the output shall
  contain the ADR reading rule.
- When an ADR or PRD line outside an invariant contains an absolute word,
  Vale shall report the line.
- When a build run reaches a finish point with an `open` ledger line, `lore
  workflow ledger-check` shall exit non-zero.
- When a `loop` round or an `epic` feature merges, the agent shall write a
  resume line to the ledger.
- When `lore hook pre-compact` runs with a ledger in the worktree, the
  message shall name the ledger path.
- When `lore hook session-start` finds a ledger with a resume line in the
  worktree, the output shall offer to resume.
- When `lore workflow risk` runs on a diff over any threshold, the command
  shall print `high` and the reason.
- While the risk level is `low`, `build` shall spawn the reviewer at the
  `mid` tier.
- When an epic merges, the `document` pass shall set the PRD status to
  `shipped`.
- While a PRD has status `shipped`, SessionStart shall not inject the PRD.
- When `build` finishes an epic, the epic issue body shall contain a `##
  Handover` section.
- When `tools/release.py --in-branch` runs, the tool shall add one bump
  commit to the current branch.
- When `lore trace tokens` runs on a transcript copy, the command shall
  print token sums per skill phase.
- The `lore-workflow` plugin shall ship nine skills.

## Out of scope

- The retirement work of PRD 0014 (issue 419). PRD 0015 starts after that
  epic merges.
- A rewrite of ADRs 0001 to 0013. Each one gets a `Holds` section as an
  amendment when the ADR misleads an agent.
- Non-GitHub trackers.
- A fixed section skeleton for PR bodies or PRDs.
- An automatic release on merge through GitHub Actions. Branch protection
  blocks bot pushes to `main`.

## Glossary changes

Added in `CONTEXT.md` § Decision records and build, in its own commit:

- home, Holds, invariant, default, incidental, amendment, shipped PRD
- ledger, ledger check, finish point, resume line, breakpoint
- build mode, risk level, handover section

Changed after feature 6 lands:

- **Workflow** — the chain becomes `orient → grilling → to-epic → build →
  document`, with `handover` at any stop.
- **Epic note** — retired; the board and the handover section hold its
  content.
- **Handover (epic seed)** — renamed to handover section.
