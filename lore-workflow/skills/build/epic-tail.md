# Epic tail

Read this when every roadmap row is `merged` and epic-branch CI is green.

## 1. Open the epic PR

Open one PR `epic/<issue> → <target_branch>` that links every sub-issue. Write the body through
[`file-issue`](../file-issue/SKILL.md) in PR-body mode.

## 2. Resolve the ledger

List the open lines of the board's `## Ledger` section with `lore workflow ledger-check -`.

- **User reachable:** present the lines in one message, as [loop-wrap-up.md](loop-wrap-up.md)
  step 3 does. Write what the user approves onto `epic/<issue>`. An ADR fills `Holds` and
  `Revisit if` per [ADR-FORMAT.md](../grilling/ADR-FORMAT.md).
- **User not reachable:** file each open line through `file-issue`, then set it `filed`.
- An epic PR that carries a new ADR waits for a human merge (decision gate).

Set each outcome on the board: pipe the comment body through `ledger-set`, then write the output
back to the comment.

```
gh api repos/<owner>/<repo>/issues/comments/<id> --jq .body \
  | lore workflow ledger-set <n> --outcome approved --board - > board.md
gh api -X PATCH repos/<owner>/<repo>/issues/comments/<id> -F body=@board.md
```

## 3. Docs, then review

- **Docs.** Run the pre-merge mode of [`document`](../document/SKILL.md) on
  `<target_branch>...epic/<issue>`. It commits onto `epic/<issue>` and marks the PRD shipped with
  `lore workflow prd-ship`. The docs step may escalate, or stay red after one fix pass. Then drop
  its commit, merge without it, and run `document` in catch-up mode afterwards.
- **Whole-epic review**, only for an epic with three or more features. Follow
  [review.md](review.md) on the epic PR, after the docs commit. The reviewer checks
  cross-feature consistency: naming, duplicate helpers, conflicting edits to shared files. It
  also checks the docs commit against the behaviour. No separate docs round. For one or two
  features, the per-PR reviews already read every diff; skip this review. Exception: the
  human-present option in `SKILL.md` runs this review for any number of features.
- A FAIL goes to the teammates that own the files. Two fix rounds at most, then block and
  escalate.

## 4. Merge

1. Add the version bump as the last commit, if the repo's `AGENTS.md` or `CLAUDE.md` requires
   one.
2. Run the ledger check on the board: `gh api repos/<owner>/<repo>/issues/comments/<id> --jq
   .body | lore workflow ledger-check -`. Do not merge while it exits 1.
3. When `epic-policy` returned `deploy_gate: true`, get one human confirmation and note it on
   the board.
4. Merge on green checks. Exception: in the human-present option in `SKILL.md`, the user merges.

## 5. Handover section

Write `## Handover` into the epic issue body, in the shape [`handover`](../handover/SKILL.md)
gives. The section names what shipped with the merge SHA, decisions with ADR links, deviations
from the PRD, follow-up issues and known limits.

## 6. Cleanup

Delete every merged feature branch, local and remote, and remove its worktree. Keep only the
history of the epic branch.

## Final checklist

Verify each item, then report:

- [ ] every roadmap checkbox ticked, every sub-issue closed
- [ ] the epic PR merged; the handover section names its merge SHA
- [ ] the ledger check passes on the board
- [ ] the board shows no `queued` or `running` row
- [ ] the PRD status is `shipped`, or the catch-up docs PR is open
- [ ] every feature branch and worktree gone, local and remote
