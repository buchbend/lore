# Mode `epic`

Read this when `build` runs in `epic` mode. The rules in [SKILL.md](SKILL.md) apply too.

You plan, dispatch, review and merge. Teammates write the feature code. Run without asking.
Stop only for completion or a stop condition.

**Map.** Read the epic body and build order. Run `lore workflow epic-policy <repo_root>` once per
repo: `{target_branch, deploy_gate}`. Feature PRs target `epic/<issue>`. `epic/<issue>` merges
into `target_branch` through one epic PR.

**Resume.** Fetch the board comment (marker below) and pipe it to `lore workflow parse-board`.
A `merged` row is done. A `queued` or `blocked` row is dispatched again. Reuse an existing
`epic/<issue>`. Read the last resume line in the `ledger` key. Write the board line below if
the local ledger lacks it. No board: fresh run.

**Gate.** `lore workflow validate-build-order --json` on the epic body. `ok: false`: report the
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

**Board line.** Record the board in the local ledger of the checkout you run in: `lore workflow
ledger-add --kind board --url <comment URL> --text "<owner/repo#epic>"`. After a compaction,
PreCompact names the board and the call that reads it. Re-read the board then, before you act.

**Codemap excerpt.** Build it once: rank `lore codemap` symbols against the epic's touchpoints
and keep about 1k tokens.

**Dispatch.** A feature is ready once its blockers merged into `epic/<issue>`. Give each ready
feature a worktree off `epic/<issue>` and a background teammate with `LORE_SUPPRESS_CAPTURE=1`
in its environment. Tier: `mid` for well-scoped work, `strong` for cross-cutting work. Fill
[teammate-brief.md](teammate-brief.md). A teammate silent for about 30 minutes is spawned once
more into the same worktree. A second death blocks the feature.

**Review.** When a feature PR opens, follow [review.md](review.md). PASS and green checks: merge
it into `epic/<issue>`.

**Merge.** If the epic branch moved, rebase the PR and wait for green checks. A rebase conflict
goes back to the teammate. For a cross-repo pin, read the `pin` of the edge between producer and
consumer (`lore map <consumer> --json`). Merge the producer and record what the pin names (SHA,
tag, image or package version) before you dispatch the consumer. Without an edge, record the
producer's SHA. After each merge:
- tick its build-order box;
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
- Then run the epic tail.

**Epic tail.** When every row is merged and CI is green, read [epic-tail.md](epic-tail.md) and
follow it.

**Stop conditions:**
- a HITL row;
- a PR still failing after two fix rounds;
- an ambiguous spec that needs a scientific or architectural call;
- a merge conflict no teammate resolves;
- a CI infrastructure failure.

Note the stop on the board, keep the other features running, and report.
