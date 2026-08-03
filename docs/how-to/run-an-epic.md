# Run an epic

**Goal:** take a shaped, multi-feature body of work through the epic chain to
a merged epic and its documentation.

Reach for this when the work is really several features, or when its shape
is still unsettled and needs shaping before any code is written. For one
small, clear change, use the [fast path](use-the-fast-path.md) instead.

## Before you start

- The repo is onboarded into the conventions
  ([Onboard a repo](onboard-a-repo.md)).
- `gh auth status` is green.
- You have a rough idea or an epic seed to start from (see the
  [Glossary](../conventions.md#glossary) in Conventions).

## Steps

1. **Shape it.** In a session pointed at the idea (or the seed issue), run
   `/lore-workflow:orient` to reflect the work back and confirm it, then
   `/lore-workflow:grilling` — say "grill with docs" — to stress-test the plan
   and record decisions inline. Do not skip this: an epic built on an
   unshaped plan wastes the autonomous build.
2. **Cross the checkpoint.** Run `/lore-workflow:to-epic`. It writes the PRD
   to `docs/prd/` via `lore workflow create-prd`, opens the epic tracker
   issue and one sub-issue per feature, and emits the roadmap dependency
   table. Review the PRD and the roadmap before continuing — this is the
   last human gate before the build.
3. **Build it.** Point a session at the epic issue and run
   `/lore-workflow:orchestrate-epic`. It validates the roadmap
   (`lore workflow validate-roadmap`), creates the `epic/<issue>` integration
   branch from the detected target branch (`develop` if present, else
   `main`), implements the features test-first (one teammate per feature when
   they run in parallel; one sequential teammate when the roadmap is a
   straight chain), crosschecks every pull request, integrates the features
   in dependency order, and opens the final pull request to the target
   branch.
4. **Docs ship with the epic.** Before that final pull request,
   `orchestrate-epic` automatically runs `/lore-workflow:document-epic`,
   which commits the Diátaxis doc updates onto the epic branch — the docs
   merge inside the epic PR, not as a trailing afterthought. Only if the docs
   stage fails does it fall back to the old post-merge docs PR
   (auto-merged on green, reviewed post-hoc).

## Notes

- **You do not babysit step 3.** The features are flagged **AFK** (they run
  autonomously). The orchestrator stops only to report completion or to
  escalate a genuine blocker — a feature that needs a human decision
  (**HITL**), a crosscheck that keeps failing, or an unresolvable conflict.
- **Read the run in the supervision trail.** The orchestrator keeps a single
  status comment on the epic issue, updated in place, recording each
  feature's state in a machine-readable table. Its own working context — the
  tier and escalation decisions, crosscheck verdicts, in-flight markers —
  rides a separate composed epic note, so a resumed run rehydrates its
  reasoning, not just the checklist.
- **If the run breaks off, resume — do not restart.** See
  [Resume a broken epic](resume-a-broken-epic.md).

## Done when

- The final epic pull request — docs included — is merged to the target
  branch.
- Every sub-issue is closed and every roadmap checkbox is ticked.
- On docs fallback only: the standalone docs PR has merged on green.
