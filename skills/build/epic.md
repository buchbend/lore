# Mode `epic`

You plan, dispatch, review and merge; teammates write the feature code. Run without asking.
Stop only for completion or a stop condition.

**Map.** Read the epic body and build order. Run `lore workflow epic-policy <repo_root>` once per
repo: `{target_branch, deploy_gate}`. Feature branches merge into `epic/<issue>` locally. That
branch reaches the remote once, as one epic PR into `target_branch`, so CI runs once per epic.
Issues, their comments and the board stay on GitHub.

**Resume.** Pipe the board comment (marker below) to `lore workflow parse-board`. `merged` is
done; `queued`/`blocked` is dispatched again. Reuse an existing `epic/<issue>`. Read the last
resume line in `ledger`. Write the board line below if the local ledger lacks it. No board: fresh run.

**Gate.** `lore workflow validate-build-order --json` on the epic body. `ok: false`: report
`problems`, stop. A feature's batch is its DAG depth. **Compact** band (one repo, every row AFK,
one feature per batch): one teammate builds the features in order, still one branch each.
Otherwise **standard**: up to four teammates at once.

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

`parse-board` reads marker and columns verbatim; the marker keeps its old name. `State` is
queued, running, review, merged or blocked. The `PR` column stays `—` until the epic PR exists.
Then create `epic/<issue>` from `target_branch`, locally. Push nothing before the epic tail.

**Spec branch.** Run `lore workflow spec-branch <body file> --repo <repo_root>` only in the repo
of the `**PRD:**` link; it prints `{branch, prd, needs_merge, ref}`; exit 1: stop. `needs_merge`:
merge the printed `ref` into the new `epic/<issue>` there, then dispatch. A conflict:
abort the merge, stop and report. No second PR; push nothing to `target_branch`.

**Board line.** Record the board in this checkout's local ledger: `lore workflow
ledger-add --kind board --url <comment URL> --text "<owner/repo#epic>"`. After a compaction,
PreCompact names the board and the read call; re-read it before you act.

**Codemap excerpt.** Once: rank `lore codemap` symbols against the epic's touchpoints; ~1k tokens.

**Dispatch.** A feature is ready once its blockers merged into `epic/<issue>`. Give it a worktree
off that branch and a background teammate with `LORE_SUPPRESS_CAPTURE=1` in its environment.
Tier: `mid` well-scoped, `strong` cross-cutting. Fill [teammate-brief.md](teammate-brief.md). A
teammate silent about 30 minutes is spawned once more in the same worktree; a second death
blocks the feature.

**Review.** Feature branch: [review.md](review.md). PASS: merge as below.

**Merge.** No CI runs here; the local run is the gate. In a worktree of `epic/<issue>`: `git
merge --no-ff feat/<n>-<slug>`, run the full suite and ruff, read the exit codes. Green: the
merge stands. Red or a conflict: `git reset --hard ORIG_HEAD`, back to the teammate.
Cross-repo: read the `pin` of the edge between producer and consumer
(`lore map <consumer> --json`). Merge the producer and record what the pin names (SHA, tag, image
or package version) before dispatching the consumer. Without an edge, record the producer's SHA.
The consumer reads the producer from its local checkout. A pin that resolves only from the
remote (image, package) is the one early push: push the producer's `epic/<issue>` and note it
on the board.
After each merge:
- tick its build-order box;
- close the sub-issue (`gh issue close <n> --comment "Merged into epic/<issue> at <sha>"`);
- set the row to `merged`;
- add the teammate's ADR and term candidates to the ledger;
- add the resume line (breakpoint);
- where the repo has migrations, check that one migration head remains.

Then dispatch what the merge made ready.

**Human present, on request.** Needs all three: the user asks for a quick run and stays
present; every row is AFK; one repo without a deploy gate holds all rows.
- Build the slices that share new files yourself, on `epic/<issue>`.
- Teammates get only independent slices.
- Your own slices may write tests beside the code. Then break two or three pieces of key logic,
  confirm the tests fail, restore, and note the check on the board.
- Then run the epic tail.

**Epic tail.** All rows merged: [epic-tail.md](epic-tail.md), at once.

**Stop conditions:**
- a HITL row;
- a feature still failing after two fix rounds;
- an ambiguous spec that needs a scientific or architectural call;
- a merge conflict no teammate resolves;
- a CI infrastructure failure.

Note the stop on the board, keep the other features running; report.
