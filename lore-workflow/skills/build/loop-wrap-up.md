# Loop wrap-up

Read this when the user says "wrap up" or ends the loop. Work in the loop worktree. `base` is the
commit in the first resume line of the ledger.

1. **Full suite and ruff**, once, in the foreground, on `loop/<slug>`. Fix red in one more round
   first.
2. **Offer the advisory pass.** Ask whether to run it; skip it on a no. On a yes, spawn two
   subagents in parallel at the `strong` tier over `<base>..loop/<slug>`:
   - **Architect:** security, code layout, duplication, correctness risks.
   - **Web design:** style consistency, accessibility, responsive layout. Skip it when the diff
     touches no UI.

   Each returns a short ranked list and changes no code. The user picks which to do as extra
   rounds.
3. **Present the ledger** in one message, from `lore workflow ledger-check`:
   - per ADR candidate: the decision, the alternatives, your verdict on each of the three
     criteria in [ADR-FORMAT.md](../grilling/ADR-FORMAT.md);
   - per term candidate: a proposed definition and `_Avoid_` words;
   - per `left` line: file it, or drop it.

   The user approves, edits or drops each line in one reply. Set each outcome with `lore workflow
   ledger-set`. File each kept `left` line through [`file-issue`](../file-issue/SKILL.md) and set
   it `filed <owner/repo#n>`.
4. **Write what was approved.** Run `lore style show writing-rules` first.
   - ADRs: `docs/adr/NNNN-kebab.md` per [ADR-FORMAT.md](../grilling/ADR-FORMAT.md). Fill `Holds`
     and `Revisit if` from the conversation.
   - Terms: `CONTEXT.md`, or the context `CONTEXT-MAP.md` names, per
     [CONTEXT-FORMAT.md](../grilling/CONTEXT-FORMAT.md).

   Write no line the user did not approve.
5. **Docs.** Run the pre-merge mode of [`document`](../document/SKILL.md) on
   `<base>..loop/<slug>`.
6. **Commit, test, merge locally** as in a round.
7. **Finish point.** `lore workflow ledger-check` passes, or you go back to step 3.
8. **Wrap-up PR.** The wrap-up PR is the one thing that reaches the remote. Add the version bump as the
   last commit if the repo's `AGENTS.md` or `CLAUDE.md` requires one. Push `loop/<slug>`. Open
   one PR `loop/<slug> → <target>`. Its body is the handover section, in the shape
   [`handover`](../handover/SKILL.md) gives, written through `file-issue` in PR-body mode.
9. **Archive the ledger:** `lore workflow ledger-archive`.
10. **Report and ask.** Report the PR, the ADRs and terms written, and the filed issues, in the
    merge approval message of [SKILL.md](SKILL.md). On a yes, merge the PR. After the merge, by
    you or the user, sync the local target with the remote. A squash merge makes the local
    target diverge: ask before `git reset --hard origin/<target>`. Remove the worktree and the
    branch when the user confirms.
