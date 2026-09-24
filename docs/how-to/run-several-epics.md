# Run several epics

**Goal:** implement two or more epics in one session. A supervisor orders
them by their dependencies and runs each epic through `orchestrate-epic`.

Reach for this when you hold several epic tracker issues that are ready to
build. For one epic, [run an epic](run-an-epic.md) directly.

## Before you start

- Each epic went through `/lore-workflow:to-epic`, so its roadmap passes
  `lore workflow validate-roadmap`.
- Every repo the epics touch is onboarded ([Onboard a repo](onboard-a-repo.md)).
- `gh auth status` is green.
- You know the epic issue numbers, in `owner/repo#n` form.

## Steps

1. **Start the run.** In a session in one of the repos, run:

   ```
   /lore-workflow:super-orchestrate owner/repo#12 owner/repo#15 owner/repo#20
   ```

   Two options go in the same line, in plain words:
   - a **tier floor**, `strong` or `frontier`. The default is `frontier`.
   - a **cap** on epics running at once. The default is 3.

   ```
   /lore-workflow:super-orchestrate owner/repo#12 owner/repo#15 tier floor strong, cap 2
   ```

2. **Wait for the roadmap gate.** The supervisor validates every epic's
   roadmap. One invalid roadmap stops the run before any agent starts. Fix
   the roadmap and start again.
3. **Confirm the epic list and the order.** An analyst subagent maps the
   dependencies between the epics. The supervisor then shows one table: epic,
   title, repo, upstream epics and overlaps. Check the titles: a wrong title
   means a wrong epic number. Answer the one question. This confirmation is
   the only one in the run.
4. **Let it run.** The supervisor posts one comment on each epic with its
   upstream epics, its overlaps and the tier floor. It then starts one epic
   lead per epic, up to the cap. Each epic lead runs `orchestrate-epic` and
   keeps that epic's own status comment.
5. **Answer escalations.** An epic lead never asks you directly. The
   supervisor asks you one question per escalation, with the lead's evidence.
   Your answer either resumes the lead or blocks the epic. The other epics keep
   running while you answer.
6. **Read the report.** At the end the supervisor lists each epic's state,
   epic PR, merge SHA and upstream epics.

## How the supervisor orders epics

- **An edge orders two epics.** The downstream epic starts only after every
  upstream epic PR has merged. Three kinds of edge exist:
  - `explicit`: an epic, its PRD or a sub-issue names the other epic as a
    blocker. A GitHub issue dependency counts too.
  - `interface`: one epic uses an API, schema, command-line interface or
    module that the other epic adds or changes.
  - `pin`: one repo pins a commit or release that the other epic produces.
- **An overlap does not order epics.** Two epics that only edit the same files
  or add migrations in one repo run side by side. The epic lead that merges
  second merges the target branch into its epic branch and resolves the
  conflicts. That fix step keeps the intent of both epics and a single
  migration head.
- **A cycle stops the run.** Split or merge the epics that form it, then start
  again.

## Choose the tier floor

The tier floor sets one tier for every agent in the run: the analyst, each
epic lead, and every teammate and reviewer under a lead. Tiers are semantic
names; your installation maps each tier to a model (see
[Model tiers](../model-tiers.md)).

- The shipped table maps `frontier` and `strong` to the strongest Opus model.
- On a smaller plan, keep the floor and remap the tiers in the vault config.
  The [all-Sonnet example](../model-tiers.md#user-overrides) runs every agent
  on Sonnet.
- A floor below `strong` is not allowed. `orchestrate-epic` requires its
  reviewers at `strong` or above.

## Notes

- **Cost.** With the defaults, three epic leads can run four teammates each,
  plus one reviewer per batch. Every review also runs the built-in
  `code-review`, which starts its own checking agents.
- **Resume, do not restart.** Run the same command with the same epic list.
  The supervisor reads its comments back and skips the mapping and the
  confirmation. A merged epic stays merged. A started epic resumes from its own
  status comment. A changed epic list triggers a fresh mapping and a fresh
  confirmation.
- **A deploy gate still asks you.** A repo with a deploy gate needs your
  confirmation before its epic PR merges. The supervisor relays the question.

## Done when

- Every epic PR is merged, or its epic is blocked with a cause you know.
- Every epic blocked behind a blocked epic is listed in the report.
- Every epic's status comment shows no queued or running rows.
