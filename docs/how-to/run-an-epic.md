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
   `/lore-workflow:orient` and confirm what it reflects back. Then run
   `/lore-workflow:grilling` and say "grill with docs". The interview
   stress-tests the plan and records terms and ADRs as decisions land.
2. **Pass the checkpoint.** Run `/lore-workflow:to-epic`. It writes the PRD
   under `docs/prd/`, opens the epic issue and one sub-issue per feature, and
   writes the roadmap table. Review the PRD and the roadmap. This review is
   the last human gate before the build.
3. **Build the epic.** Point a session at the epic issue and run
   `/lore-workflow:build` in `epic` mode. The skill:
   - validates the roadmap with `lore workflow validate-roadmap`;
   - creates `epic/<issue>` from the target branch;
   - builds the features test-first, one teammate per feature;
   - reviews each PR at the depth `lore workflow risk` sets;
   - merges the features in dependency order and opens the epic PR.
4. **Docs ship with the epic.** Before the epic PR merges, `build` runs
   `/lore-workflow:document` in pre-merge mode. The docs commit lands on the
   epic branch, and the PRD status becomes `shipped`.
5. **Read the handover.** After the merge, `build` writes a `## Handover`
   section into the epic issue body: what shipped with the merge SHA, the
   decisions, the deviations from the PRD, the follow-ups and the known
   limits.

## Notes

- **The build runs without you.** AFK features run on their own. `build`
  stops for a HITL feature, a review that keeps failing, or a conflict no
  teammate resolves.
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
- Every sub-issue is closed and every roadmap checkbox is ticked.
- The epic issue body holds the handover section.
