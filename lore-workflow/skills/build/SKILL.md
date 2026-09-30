---
name: lore-workflow:build
description: Build code in one of three modes. `loop` — fast rounds with the human watching the
  live dev stack, local merges, one wrap-up PR. `issue` — one clear issue to one PR. `epic` — an
  epic tracker with a roadmap, teammates per feature, review by risk level, one epic PR. Every
  mode keeps a ledger, writes resume lines, and ends with a handover section. Use when the user
  asks to build, implement, fix, or run an epic, an issue, or quick feedback rounds.
---

# Build

The user names the mode (`/lore-workflow:build epic owner/repo#n`), or you pick it from the table.
Then follow its section. Read a sibling file only when the run reaches it.

| Mode | Use when | Finish point |
|---|---|---|
| `loop` | The user reacts to a running feature: screenshots, "make this red", "quick fix". | Wrap-up PR from `loop/<slug>` |
| `issue` | One written, clear issue. | One PR, merged after the user approves |
| `epic` | An epic tracker issue with a roadmap table from [`to-epic`](../to-epic/SKILL.md). | Epic PR, then the handover section |

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
- **Tiers.** Every spawn names a tier and passes `lore tier resolve <tier>` as its model. See
  [TIER-DELEGATION.md](../../TIER-DELEGATION.md).
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
  `--board -`, pipe the board comment in, and write the printed body back to the comment. The
  commands are in [epic-tail.md](epic-tail.md) § 2.
- **Handover section.** Each finish point writes `## Handover` in the shape
  [`handover`](../handover/SKILL.md) gives. Work that stops early runs `handover` instead.
- **Version bump.** If the repo's `AGENTS.md` or `CLAUDE.md` requires a version bump per release,
  the shipping PR carries it as its last commit. Follow that file. Lore itself runs
  `python3 tools/release.py --in-branch --notes notes.md`.
- **Follow-ups** go through [`file-issue`](../file-issue/SKILL.md) and get a `filed` ledger line.
- **Session end.** Read `lore config get feedback.retrieval_misses`. When true, file one issue per
  retrieval miss on `feedback.retrieval_misses_repo` through `file-issue`: the fact, the tools
  tried, the turn count. When false, skip the check. List every issue and PR the session created
  or commented on in your final message.

## Mode `loop`

Small asks, the human watching. No PRD, no teammates, no PR per round.

**Stop and advise design** before you code an ask with any of these signs:
- a new data model, table, API endpoint or persisted state;
- several features or component boundaries;
- a choice between real alternatives the user has not made;
- auth, permissions, secrets or other users' data;
- no way to state it as one testable behaviour.

Name the sign in one line. Recommend
`orient` then `issue`, or `grilling` then `to-epic`. Log the ask as a `left` line and carry on
with the small asks. The user may say "do it anyway"; then slice it into small rounds.

**Setup, once.**
1. The target is the branch the dev stack serves: the branch of the primary checkout (`git
   worktree list`, first row). Only `develop` or `main` qualify. Anything else, or both look
   plausible: ask.
2. Create branch `loop/<slug>` off the target in a sibling worktree. Edit only there.
3. Start the ledger in the worktree: `lore workflow ledger-add --kind resume --text "base <sha>
   of <target>" --next "round 1"`.

**One round.**
1. Restate the asks, one line each. Pick and name a default for an ambiguous ask. Ask only when
   a wrong guess is costly to undo.
2. Read the code each ask touches, shared helpers too. A helper that rebuilds a record from
   known fields drops a new field; grep its callers.
3. Test first where there is behaviour: a handler, a state change, an API field, a key path, a
   phone-width branch. Assert a CSS rule that carries behaviour. Pure looks get no test; list
   them as "visual only".
4. Run only this round's tests, plus ruff on the touched files. Read the result, then commit.
   Do not chain the commit behind the test command. One commit per round.
5. Merge into the target locally: in the primary checkout, `git merge --ff-only loop/<slug>`.
   A dirty checkout or a non-fast-forward: stop and ask. Restart the dev stack the way the repo
   says. Nothing reaches the remote until wrap-up.
6. Spawn one background subagent at the `cheap` tier. It runs the full suite and ruff on the
   merged commit, read-only, and reports failing test ids or "green". A new round replaces a
   running check. A failure becomes the first ask of the next round.
7. Add ledger lines for this round's candidates, then the resume line (breakpoint).
8. Report: what changed, what the tests verify, what is visual only, the defaults you picked,
   new ledger lines, the last background result. Ask for the next round.

**Traps.** A value read back right after a router write is still the old value; keep the new
one in a local. A leave transition removes its element a frame later. A test that stubs globals
restores them on failure too. Without a browser the look is unverified; say so.

On "wrap up", read [loop-wrap-up.md](loop-wrap-up.md) and follow it.

## Mode `issue`

1. **Intake.** Read the issue (`gh issue view <n> --json title,body`). Run `lore codemap` to find
   the files. No explorer fan-out.
2. **Clarify only if needed.** Ambiguous intent or criteria: ask at most three questions. Clear
   issue: ask none.
3. **Branch.** `lore workflow epic-policy <repo_root>` gives `target_branch`. Create
   `feat/<issue>-<slug>` off it in a worktree. Implement with [`tdd`](../tdd/SKILL.md), one PR.
4. **Docs.** Run the pre-merge mode of [`document`](../document/SKILL.md) on the branch diff.
5. **Open the PR**, linking the issue. Write the body through `file-issue`: what the change does.
6. **Review.** Follow [review.md](review.md). Fix the findings. Keep `gh pr checks` green.
7. **Finish point.** Present the open ledger lines to the user in one message, as
   [loop-wrap-up.md](loop-wrap-up.md) steps 3 and 4 do. Push the approved ADRs and terms to the
   same PR. `lore workflow ledger-check` passes, or you go back to the user. Add the handover
   section to the PR body (`gh pr edit <n> --body-file …`). Add the version bump as the last
   commit if the repo needs one. Run `lore workflow ledger-archive`. Ask for the merge
   approval and merge on a yes.

## Mode `epic`

You plan, dispatch, review and merge. Teammates write the feature code. Run without asking.
Stop only for completion or a stop condition.

**Map.** Read the epic body and its roadmap. HITL rows are escalation points. Run `lore workflow
epic-policy <repo_root>` once per repo: `{target_branch, deploy_gate}`. Feature PRs target
`epic/<issue>`. `epic/<issue>` merges into `target_branch` through one epic PR.

**Resume.** Fetch the board comment (marker below) and pipe it to `lore workflow parse-board`.
A `merged` row is done. A `queued` or `blocked` row is dispatched again. Reuse an existing
`epic/<issue>`. Read the last resume line in the `ledger` key. No board: fresh run.

**Gate.** `lore workflow validate-roadmap --json` on the epic body. `ok: false`: report the
`problems` and stop. A feature's batch is its depth in the DAG. **Compact** band: one repo,
every row AFK, one feature per batch. Then one teammate builds the features in order, still one
branch and one PR each. Otherwise **standard**: up to four teammates at once.

**Board.** One comment on the epic issue, edited in place, never a second one:

```
<!-- lore-orchestrate-epic:status v1 -->

| Feature | Issue | Tier | Batch | State | PR |
|---|---|---|---|---|---|
| <title> | owner/repo#n | <tier> | <batch> | queued | — |

## Ledger

- [resume] <UTC timestamp> — <state>; next: <next ask>

## Notes

- <blocker or escalation only>
```

`lore workflow parse-board` reads the marker and the columns verbatim; the marker keeps its
old name. `State` is queued, running, review, merged or blocked. Then create and push
`epic/<issue>` from `target_branch`.

**Codemap excerpt.** Build it once: rank `lore codemap` symbols against the epic's touchpoints
and keep about 1k tokens. Every teammate gets the same excerpt.

**Dispatch.** A feature is ready once its blockers merged into `epic/<issue>`. Give each ready
feature a worktree off `epic/<issue>` and a background teammate with `LORE_SUPPRESS_CAPTURE=1`
in its environment. Tier: `mid` for well-scoped work, `strong` for cross-cutting work. Fill
[teammate-brief.md](teammate-brief.md). A teammate silent for about 30 minutes is spawned once
more into the same worktree. A second death blocks the feature.

**Review.** When a feature PR opens, follow [review.md](review.md). PASS and green checks: merge
it into `epic/<issue>`.

**Merge.** Merge each passing feature PR into `epic/<issue>`. If the epic branch moved, rebase
the PR and wait for green checks. A rebase conflict goes back to the teammate. For a cross-repo
pin, merge the producer and record its SHA before you dispatch the consumer that pins it. After
each merge:
- tick the roadmap checkbox;
- close the sub-issue (`gh issue close <n> --comment "Merged via PR #<pr>"`);
- set the row to `merged`;
- add the teammate's ADR and term candidates to the ledger;
- add the resume line (breakpoint);
- check that epic-branch CI is green and, where the repo has migrations, that one migration head
  remains.

Then dispatch every feature the merge made ready.

**Human present, on request.** This option needs three things. The user asks for a quick run
and stays present. Every row is AFK. One repo without a deploy gate holds all rows.
- Build the slices that share new files yourself, on `epic/<issue>`.
- Give teammates only the independent slices. They commit without a PR; you merge their
  branches with `--no-ff`.
- Your own slices may write tests beside the code. Then break two or three pieces of key logic,
  confirm the tests fail, restore, and note the check on the board.
- Then run the epic tail. Its whole-epic review is the one review of this run, for any number
  of features. The epic PR merges only after the merge approval.

**Epic tail.** When every row is merged and CI is green, read [epic-tail.md](epic-tail.md) and
follow it.

**Stop conditions:**
- a HITL row;
- a PR still failing after two fix rounds;
- an ambiguous spec that needs a scientific or architectural call;
- a merge conflict no teammate resolves;
- a CI infrastructure failure.

Note the stop on the board, keep the other features running, and report.
