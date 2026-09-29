---
name: lore-workflow:quick-orchestrate
description: Light variant of orchestrate-epic for a small epic the human wants built fast
  while staying present — the lead writes the slices that share new code itself, delegates
  only independent slices, merges locally into the epic branch, and runs one independent
  review plus the docs pass on a single epic PR. Use when the user asks for a "straight",
  "quick" or "cut corners" run of an epic, or when most slices build one fresh package.
---

# Quick Orchestrate

The lighter sibling of [`orchestrate-epic`](../orchestrate-epic/SKILL.md). Same input — an
epic tracker issue with a roadmap from [`to-epic`](../to-epic/SKILL.md) — same board, same
epic branch, same epic PR. It cuts the per-feature ceremony that pays only when many
teammates run side by side: one PR per feature and one crosscheck per batch.

**The human asks for it.** Run it only on an explicit request (“straight implementation”,
“cut corners”, “quick orchestrate”). Otherwise use `orchestrate-epic`.

## When it fits

- The epic has **≤ 5 slices** in one or two repos, all AFK.
- Most slices build **one fresh package or module**. Split across teammates, they would edit
  the same new files and rebase on each other.
- The human stays present and merges the epic PR.

It does not fit an epic with HITL slices, a deploy gate, cross-repo pins, or wide changes to
shared, well-used code. Those need the per-PR crosscheck of `orchestrate-epic`.

## What it keeps, what it cuts

| Kept from `orchestrate-epic` | Cut |
|---|---|
| Roadmap gate (`lore workflow validate-roadmap`) and `epic-policy` | One PR per feature |
| The board comment, parse-board compatible, edited in place | One crosscheck reviewer per batch |
| `epic/<issue>` branch and one final epic PR | Autonomous merge of the epic PR |
| Never merge on red CI; ruff clean | Strict red-first for the lead's own greenfield code (see Tests) |
| Docs ship with the epic | Whole-epic review split from crosscheck |
| One independent review before merge | |

Record the run mode and every deviation in the board's notes section.

## Flow

**1. Map.** Read the epic and its roadmap. Run `lore workflow validate-roadmap --json` and
`lore workflow epic-policy <repo_root>`. A malformed roadmap stops the run, as in
`orchestrate-epic`. Before building, check that the PRD number and file paths do not collide
with open pull requests (`gh pr list --search "PRD <n>"`).

**2. Split into lanes.** Sort the slices into two lanes:
- **Lead lane** — the slices that share new files. The lead writes them in one pass on
  `epic/<issue>`, one commit per coherent change. Splitting them buys only rebases.
- **Teammate lane** — a slice in an independent code area, such as a backend change beside a
  new client. Spawn one teammate per such slice at `mid` tier for a well-scoped change,
  `strong` for a cross-cutting one (`lore tier resolve <tier>`). Give it a worktree and branch
  `feat/<n>-slug` off `epic/<issue>`. The teammate commits and reports; it opens no PR.

Emit the board: lead-lane rows `running` with tier `lead`, teammate rows with their tier.

**3. Build.** Start the teammates first, in the background. Then build the lead lane while they
run. Before writing a client of an existing API, read the routes and models it calls: request
bodies, index bases, conflict status codes, upload format. Correct the sub-issue text where it
disagrees with the code. Record the deviation on the board rather than build to the wrong spec.

**4. Tests.** Every slice leaves tests that map to its acceptance criteria.
- The teammate lane follows strict TDD via [`tdd`](../tdd/SKILL.md).
- The lead lane may write tests beside the code. It then owes a **mutation check**: break two
  or three pieces of key logic, confirm the tests fail, restore. Record the check on the board.
- Where a live dev stack runs, smoke-test the new surface against it. Report every write the
  smoke test left behind.

**5. Integrate.** When a teammate reports, merge its branch into `epic/<issue>` with
`--no-ff`. Run its touched tests plus the route or permission tests on the merged tree. Push.
Remove the worktree and the branch.

**6. Docs.** The lead runs the docs pass on `epic/<issue>` while teammates still build. Use the
[`document-epic`](../document-epic/SKILL.md) method: classify the change into the Diátaxis
four, and fix every existing page the change makes false. A new client usually needs a how-to,
a reference table, and an update to the agent skill document.

**7. Epic PR and review.** Open one PR `epic/<issue> → <target_branch>`. The PR closes every
sub-issue and lists every deviation. Write the body through [`file-issue`](../file-issue/SKILL.md)
in PR-body mode. Then spawn **one independent reviewer** at `strong` tier with a fresh
context, never a fork of the lead. The reviewer reads the cumulative diff and checks:
- every call against the route and model it hits;
- secrets: where a token is stored, sent and printed;
- error paths: non-JSON answers, redirects, empty bodies, bad input;
- output that renders untrusted text;
- docs statements against the code.

The reviewer returns a ranked list split into must-fix and nice-to-have, and edits nothing.

**8. Fix.** Fix every must-fix finding red-first, and each nice-to-have that costs little. A
finding in shared behavior belongs in the shared code, not in one caller. Push, and wait for
green CI on the new head. Confirm the run belongs to that head (`gh run view <id> --json headSha`).

**9. Hand over.** Finalize the board: every row `merged` with the epic PR, notes on the review
outcome and CI. The human merges. If the target branch moves before the merge, merge it into
`epic/<issue>`, resolve conflicts, rerun the touched tests, and push. A conflict in a
reference doc takes the target's text; re-apply the epic's edits on top of it.

## Environment pitfalls

- A shell outside the sandbox may resolve `$TMPDIR` differently. Pass absolute paths to
  commands that run outside it, such as `gh issue create --body-file`.
- A read-only zero-byte `.git/config.lock` blocks branch config writes. Delete it when no git
  process runs.
- A test database container may refuse its published port. Run it with host networking and
  set its port through the database environment instead.

## Stop conditions

A teammate that fails its slice twice; a must-fix finding that needs a product or scientific
decision; red CI that the lead cannot trace to this epic. Report and pause; the human decides.
