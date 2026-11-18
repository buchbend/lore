# Run an epic

**Goal:** take a multi-feature body of work through the chain to a merged
epic, its docs and its handover section.

Use this path when the work is several features, or when its shape still
needs work before any code. For one small, clear change, use the
[fast path](use-the-fast-path.md).

## Before you start

- The repo is onboarded ([Onboard a repo](onboard-a-repo.md)).
- `gh auth status` is green.
- You have an idea or a seed issue to start from.

## Steps

1. **Shape the work.** Point a session at the idea or the seed issue. Run
   `/lore:orient` and confirm what it reflects back. Then run
   `/lore:grilling` and say "grill with docs". The interview
   stress-tests the plan and records terms and ADRs as decisions land.
2. **Pass the checkpoint.** Run `/lore:to-epic`. It writes the PRD
   under `docs/prd/`, opens the epic issue and one sub-issue per feature, and
   writes the build-order table. One epic can hold several topics, with one PRD
   section per topic. `to-epic` splits the work into two epics only for a release
   order: one part waits for another part's deploy, a user decision gates most rows,
   or the repos share no release. A row can hold numbered substeps; one teammate
   builds them on one branch, one commit each. `to-epic` also proposes a review
   plan: the review lenses and the rows each lens covers. Review the PRD, the build
   order and the review plan. This review is the last human gate before the build.
3. **Build the epic.** Point a session at the epic issue and run
   `/lore:build` in `epic` mode. The skill:
   - validates the build order with `lore workflow validate-build-order`;
   - creates `epic/<issue>` from the target branch, and merges the spec branch into it
     when the target branch lacks the PRD (starting the build approves the spec on that
     branch);
   - builds the features test-first, one teammate per feature;
   - reviews each PR at the depth `lore workflow risk` sets;
   - merges each feature into the epic branch once the full local suite and
     ruff pass, without waiting for CI;
   - after each merge, runs `lore workflow after-merge`: the command ticks the
     build-order box, closes the sub-issue, sets the board row to `merged` and
     adds the resume line;
   - at the end, runs each lens of the review plan once, over the rows it tags;
   - merges the features in dependency order and opens the epic PR. CI is
     checked there: the epic PR merges only on green CI.
4. **Docs ship with the epic.** Before the epic PR merges, `build` runs
   `/lore:document` in pre-merge mode. The docs commit lands on the
   epic branch, and the PRD status becomes `shipped`.
5. **Read the handover.** After the merge, `build` writes a `## Handover`
   section into the epic issue body: what shipped with the merge SHA, the
   decisions, the deviations from the PRD, the follow-ups and the known
   limits.

## Notes

- **The build runs without you.** AFK features run on their own. `build`
  stops for a HITL feature, a review that keeps failing, or a conflict no
  teammate resolves. Feature branches merge into the epic branch locally,
  without you and without a PR; the epic branch is pushed once, at the end.
  The epic PR into the target branch waits for your merge approval in the
  conversation. Without you, the epic PR stays open.
- **Read the run on the board.** `build` keeps one status comment on the
  epic issue: the feature table, a `## Ledger` section and a `## Notes`
  section for blockers and escalations.
- **The ledger gates the finish.** ADR candidates, term candidates and
  left-over items collect in the ledger. The epic does not finish while a
  line is `open`.
- **Resume, do not restart.** See [Resume a build run](resume-a-broken-epic.md).
- **Several epics.** Start one `build` run per epic. Start a downstream epic
  after its upstream epic merged. Epics that only edit the same files can run
  side by side; the second one to merge resolves the conflicts.

## Done when

- The epic PR, docs included, merged into the target branch.
- Every sub-issue is closed and every build-order checkbox is ticked.
- The epic issue body holds the handover section.
