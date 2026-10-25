---
name: lore-workflow:build
description: Build code in one of three modes. `loop` — fast rounds with the human watching the
  live dev stack, local merges, one wrap-up PR. `issue` — one clear issue to one PR. `epic` — an
  epic tracker with a build order, teammates per feature, review by risk level, one epic PR. Every
  mode keeps a ledger, writes resume lines, and ends with a handover section. Use when the user
  asks to build, implement, fix, or run an epic, an issue, or quick feedback rounds.
---

# Build

The user names the mode (`/lore-workflow:build epic owner/repo#n`), or you pick it from the table.
Then read the mode's file and follow it, with the rules below. Read any other sibling file only
when the run reaches it.

| Mode | Use when | Finish point |
|---|---|---|
| [`loop`](loop.md) | The user reacts to a running feature: screenshots, "make this red", "quick fix". | Wrap-up PR from `loop/<slug>` |
| [`issue`](issue.md) | One written, clear issue. | One PR, merged after the user approves |
| [`epic`](epic.md) | An epic tracker with a build-order table from [`to-epic`](../to-epic/SKILL.md). | Epic PR, then the handover section |

Work that is several features, or still unshaped, goes back to [`orient`](../orient/SKILL.md).
Several epics take one `build` run each. Start a downstream epic after its upstream epic merged.

## Rules for every mode

- **Test first.** Use [`tdd`](../tdd/SKILL.md) for every behaviour a test can observe.
- **Never merge on red.** CI status and ruff come from `gh pr checks <n>`, never from a model.
  Where CI has no ruff job, run `ruff check` and `ruff format --check` and read the exit codes.
- **Decision gate.** An ADR, a PRD or a wiki topic-note edit outside a grilling session is a PR.
  A human approves its merge. Draft an ADR only after the user approved its ledger line.
- **Merge approval.** A PR that ends a `loop` or `issue` run, or an epic PR, merges only after
  the user approves it in the conversation. Post one message: the PR link, `gh pr diff <n>
  --name-only`, the review verdict, `gh pr checks <n>`, the ledger outcomes. Ask whether to
  merge. On a clear yes, merge with the repo's usual method and report the merge SHA. Silence or
  a reply about something else is not a yes. The user can merge on GitHub instead; then go on
  from the merged state.
- **Tiers.** A spawn's model is `lore tier resolve <tier>`; its description opens with its
  stage tag. See [TIER-DELEGATION.md](../../TIER-DELEGATION.md). Spawn only the subagents a mode
  names, or for parallel or isolated work. For one lookup, grep or read directly.
- **Writing rules.** Run `lore style show writing-rules` before you write a PR body, an ADR, a
  glossary term or the handover section. Write PR bodies and follow-up issues through
  [`file-issue`](../file-issue/SKILL.md). The board and the verdict block are machine-read and
  exempt.
- **Ledger.** Log each ADR candidate, term candidate and left-on-the-table item as one `open`
  line. An ADR candidate meets all three criteria in
  [ADR-FORMAT.md](../grilling/ADR-FORMAT.md). A term candidate is a domain word `CONTEXT.md`
  lacks or uses loosely.
  - `loop`, `issue`: `lore workflow ledger-add --kind adr|term|left --text "…"`. The file is
    `<git-dir>/lore-ledger.md` of the worktree.
  - `epic`: `lore workflow ledger-add --kind … --text "…" --path -` prints the line. Paste it into
    the `## Ledger` section of the board.
- **Breakpoint.** After each merged round or feature, add a resume line: `lore workflow
  ledger-add --kind resume --text "<state>" --next "<next ask>"` (epic: with `--path -`). Then
  tell the user that `/clear` is safe. After `/clear`, resume from the last resume line.
- **Finish point.** Run `lore workflow ledger-check` (epic: pipe the board comment into `lore
  workflow ledger-check -`). Do not finish while it exits 1. Set each outcome with `lore workflow
  ledger-set <n> --outcome approved|dropped|"filed <owner/repo#n>"`. In `epic` mode, add
  `--board -` as [epic-tail.md](epic-tail.md) § 2 shows.
- **Handover section.** Each finish point writes `## Handover` in the shape
  [`handover`](../handover/SKILL.md) gives. Work that stops early runs `handover` instead.
- **Logbook.** After the handover section, before the merge approval: `lore log add`, [logbook.md](logbook.md).
- **Version bump.** If the repo's `AGENTS.md` or `CLAUDE.md` requires a version bump,
  the shipping PR carries it as its last commit. Lore itself runs
  `python3 tools/release.py --in-branch --notes notes.md`.
- **Follow-ups.** A defect or a concrete change becomes an issue with a `filed` ledger line. A
  feature idea outside the run's scope goes to the roadmap: `lore roadmap add` on the build
  branch. Set its `left` line `approved`. After the merge, run `handover` § Next run.
- **Session end.** Read `lore config get feedback.retrieval_misses`. When true, file one issue per
  retrieval miss on `feedback.retrieval_misses_repo` through `file-issue`: the fact, the tools
  tried, the turn count. When false, skip the check. List every issue and PR the session
  created or commented on in your final message.
