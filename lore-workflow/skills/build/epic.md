# Mode `epic`

Read this when `build` runs in `epic` mode. [SKILL.md](SKILL.md) rules apply too.

You plan, dispatch, review and merge; teammates write the feature code. Run without asking. Stop only for completion or a stop condition.

**Map.** Read the epic body and build order. Run `lore workflow epic-policy <repo_root>` once per
repo: `{target_branch, deploy_gate}`. Feature PRs target `epic/<issue>`, which merges into
`target_branch` through one epic PR.

**Resume.** Fetch the board comment (marker below), pipe it to `lore workflow parse-board`.
`merged` is done; `queued` or `blocked` is dispatched again. Reuse `epic/<issue>` if it exists.
Read the last resume line in the `ledger` key. Write the board line below if the local ledger
lacks it. No board: fresh run.

**Gate.** `lore workflow validate-build-order --json` on the epic body. `ok: false`: report
`problems`, stop. A feature's batch is its DAG depth. **Compact** band: one repo,
every row AFK, one feature per batch. Then one teammate builds the features in order, still one
branch and one PR each. Otherwise **standard**: up to four teammates at once.

**Board.** One comment on the epic issue, edited in place:

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

`parse-board` reads the marker and columns verbatim; the marker keeps its old name. `State` is
queued, running, review, merged or blocked. Then create and push `epic/<issue>` from `target_branch`.

**Spec branch.** `lore workflow spec-branch <body file> --repo <repo_root>` prints `{branch,
prd, needs_merge}`; exit 1: stop. `needs_merge`: after you create `epic/<issue>`, merge
`origin/<branch>` into it before any dispatch. No second PR; push nothing to `target_branch`.

**Board line.** Record the board in the local ledger of this checkout: `lore workflow
ledger-add --kind board --url <comment URL> --text "<owner/repo#epic>"`. After a compaction,
PreCompact names the board and the call that reads it; re-read it before you act.

**Codemap excerpt.** Once: rank `lore codemap` symbols against the epic's touchpoints; keep
about 1k tokens.

**Dispatch.** A feature is ready once its blockers merged into `epic/<issue>`. Give it a worktree off
`epic/<issue>` and a background teammate with `LORE_SUPPRESS_CAPTURE=1`
in its environment. Tier: `mid` well-scoped, `strong` cross-cutting. Fill
[teammate-brief.md](teammate-brief.md). A teammate silent about 30 minutes is spawned once more in
the same worktree; a second death blocks the feature.

**Review.** A feature PR follows [review.md](review.md). PASS: merge as below.

**Merge.** Never wait for CI here. In a worktree of `epic/<issue>`: `git pull --ff-only`, `git
merge --no-ff feat/<n>-<slug>`, run the full suite and ruff, read the exit codes. Green: `git
push`; GitHub marks the PR merged. Red or a conflict: `git reset --hard ORIG_HEAD`, back to the
teammate. Cross-repo: read the `pin` of the edge between producer and consumer
(`lore map <consumer> --json`). Merge the producer and record what the pin names (SHA, tag, image or
package version) before dispatching the consumer. Without an edge, record the producer's SHA. After each merge:
- tick its build-order box;
- close the sub-issue (`gh issue close <n> --comment "Merged via PR #<pr>"`);
- set the row to `merged`;
- add the teammate's ADR and term candidates to the ledger;
- add the resume line (breakpoint);
- where the repo has migrations, check that one migration head remains.

At the next dispatch glance at `gh run list --branch epic/<issue> -L 3`. A red run the local
suite missed gets a fix; the epic PR merges on green CI only.

Then dispatch every feature the merge made ready.

**Human present, on request.** Needs all three: the user asks for a quick run and stays
present; every row is AFK; one repo without a deploy gate holds all rows.
- Build the slices that share new files yourself, on `epic/<issue>`.
- Teammates get only independent slices. They commit without a PR; you merge with `--no-ff`.
- In your own slices break two or three pieces of key logic, confirm the tests fail, restore,
  note it on the board. Then run the epic tail.

**Epic tail.** Every row merged: follow [epic-tail.md](epic-tail.md) at once.

**Stop conditions:**
- a HITL row;
- a PR still failing after two fix rounds;
- an ambiguous spec that needs a scientific or architectural call;
- a merge conflict no teammate resolves (the spec merge too);
- a CI infrastructure failure.

Note the stop on the board, keep the others running, report.
