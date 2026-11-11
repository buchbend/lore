# Mode `issue`

Read this when `build` runs in `issue` mode. The rules in [SKILL.md](SKILL.md) apply too.

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
   same PR. `lore workflow ledger-check` passes, or you go back to the user. Remove the shipped
   roadmap item from its `ROADMAP.md`, if the issue ships one: the item that links the issue or
   that the issue names. Commit the change. Add the handover section to the PR body. Run
   `lore log add`. Run `lore workflow ledger-archive`. Ask for the merge approval and merge on a yes.
