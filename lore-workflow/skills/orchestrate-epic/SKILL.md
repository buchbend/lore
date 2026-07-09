---
name: lore-workflow:orchestrate-epic
description: Supervise parallel TDD implementation of an epic tracker issue across one or
  more repos — plans, dispatches teammates, crosschecks every PR, and lands the epic
  autonomously. Use when the user points at an epic/tracker issue and wants orchestrated,
  batched, autonomous implementation.
---

# Orchestrate Epic

You are the **orchestrator**: you plan, dispatch, crosscheck, integrate. Teammate agents
write the feature code; you do not. Your job is supervision and integration.

**Input:** an epic tracker issue (number or URL), possibly spanning repos.
**Mode:** fully autonomous — run the whole loop without asking. Stop only to report
completion or to escalate a hard blocker (see Stop conditions).

## Invariants
- **Target branch is detected, never assumed.** Resolve the target branch once, at Map
  time: `develop` if that branch exists on the remote, else the repo default (`main`).
  Apply this same detection to BOTH the epic-branch cut point (cut `epic/<issue>` from
  the detected target) AND the final PR target (the Land-stage PR lands on the detected
  target). The workflow never creates `develop` — standing one up is a deploy-pipeline
  decision outside this workflow's scope, and the epic branch already provides
  integration buffering.
- Integration target is an **epic branch** `epic/<issue>`, cut from the detected target
  branch (`develop` if present, else `main`). Feature PRs target the epic branch; the
  epic branch lands on the detected target branch via one final PR.
- **Merges are pre-authorized.** Feature→epic merges and the epic→target merge are
  pre-authorized by this skill — the orchestrator merges on a passing crosscheck and must
  never ask the user to confirm either kind of merge. The only exception is the
  deploy-gate marker below, which can require one human confirmation before the final
  epic→target merge specifically.
- **Deploy-gate marker.** A repo whose target-branch merge triggers a deployment may
  declare, in its `AGENTS.md`, a `## Epic merge policy` section containing the line
  `epic-merge-policy: confirm`. Marker present → the final epic→target merge (only that
  merge; feature→epic merges stay pre-authorized regardless) requires one human
  confirmation before merging — the deploy gate. Marker absent (the default) → fully
  autonomous, merge without asking. Read this marker at Map time alongside target-branch
  detection; never guess deploy semantics.
- One feature = one teammate = one worktree = one branch `feat/<sub-issue>-slug` = one PR.
- **Compact mode narrows fan-out only.** A small epic (see Effort bands in Map) may run
  with one teammate implementing every feature of the band sequentially in one worktree —
  but one branch `feat/<sub-issue>-slug` and one PR per feature is unchanged, strict TDD
  stays strict, and every PR is still crosschecked before merge (see Dispatch).
- Strict TDD (red→green→refactor) per feature. No green PR without tests mapping to its
  acceptance criteria.
- Never merge on red CI.

## Loop

**Map.** Read the epic with `gh ... --json` (see `CONVENTIONS.md`). Extract roadmap
items, per-feature acceptance criteria, dependency edges, target repo(s). If the epic was
produced by `/lore-workflow:to-epic`, its **Roadmap** table is the canonical DAG (Feature | Issue | Repo |
Type | Blocked by); read acceptance criteria from each linked sub-issue, and treat
HITL-flagged features as escalation points, not auto-implemented.

**Resume.** This is the resume entry point — reached first, before the Roadmap-validator
gate, before cutting the epic branch, before creating a fresh status comment. Three steps:
1. **Find the status comment** — list comments on the epic issue (`gh api
   repos/<owner>/<repo>/issues/<issue>/comments`) for one carrying this skill's per-feature
   state table from a prior run.
2. **Reconcile** merged/queued/blocked against the epic branch's actual state: a feature
   already merged into `epic/<issue>` is done — skip it, never redispatch it; a feature still
   queued or blocked is redispatched from where it left off. The epic branch itself, if it
   already exists, is reused, never re-cut, so a resumed run never collides with half-merged
   state.
3. **Continue editing the same comment** — resume by editing that same existing comment in
   place (`gh api ... -X PATCH`) for every further update; a resumed run never opens a second
   one.

No prior status comment found → this is a fresh run; proceed to the Roadmap-validator gate
and create the status comment as described below.

**Roadmap-validator gate.** Before dispatching any teammate, validate that roadmap table
with the deterministic, dependency-free `roadmap_validator.py` shipped beside the `to-epic`
skill — required columns, fully-qualified `owner/repo#n` refs, blocked-by edges that resolve
to rows, an acyclic DAG. **Refuse to start on a malformed or cyclic roadmap:** report the
validator's problems and stop rather than dispatch teammates against a table it rejects.

**Effort bands.** Once the roadmap validates, classify the epic into an effort band from
that same table — feature count, the `Repo` column, and `Blocked by` edge count — decided
once, at Map time, before any dispatch. This skill runs two bands:
- **compact** — the lowest band this skill handles: the roadmap has ≤ 2 features, one
  repo, every row's `Type` is AFK, no HITL flags. Runs the compact dispatch path (see
  Dispatch).
- **standard** — everything else: the full batched Loop described here, concurrency cap N.

Work smaller than compact — a single well-understood issue, no roadmap DAG worth cutting —
does not enter this skill; that is the fast path (`implement-issue`, a separate skill), not
a further-compacted band. Record the chosen band on the status comment alongside the
per-feature state.

Build the feature
DAG; split into dependency-ordered batches (features in a batch are independent). Detect
the target branch (`develop` if it exists on the remote, else `main`) and cut and push
`epic/<issue>` from the up-to-date detected target. **The supervision trail is durable, not
a temp file:** create one status comment on the epic issue (feature × repo × state:
queued/running/PR/crosscheck/merged/blocked) via `gh issue comment <issue> --body ...`, then
edit that same comment in place — `gh api repos/<owner>/<repo>/issues/comments/<id> -X PATCH
-f body=...` — never opening a new one. Edit points are deliberately few, not one per
intermediate transition: at batch start, on each merge, on each blocker, and at epic
completion. Record tier decisions and deviations there as they happen.

**Context pack (built once at Map time, reused by every teammate).** Assemble one
short context pack now, so codebase discovery happens once for the whole epic
instead of once per teammate. Source it from the deterministic code map (`CODEMAP.md`,
refreshed deterministically by `scripts/code_map.py`): rank that map's symbols
against this epic's shared touchpoints and pull only the top **token-budgeted
excerpts (~1k tokens)** — never paste the whole map, which would swamp every
brief. Shape the pack as Anthropic's four-part subagent brief checklist — their
documented fix for subagents duplicating each other's searches — so every
teammate reads the same framing:
- **Objective** — the epic's shared goal and how each feature slices through it.
- **Expected output format** — one branch `feat/<n>-slug` and one PR per feature,
  strict TDD with red→green evidence.
- **Tool/source guidance** — the ranked code-map excerpts (files, key symbols,
  conventions), the `CODEMAP.md` index to widen from, and where the domain
  language lives (CONTEXT.md / glossary, `docs/adr`).
- **Task boundaries** — the scope fence and the shared-file touchpoints to stay
  clear of.
Paste this same pack, unchanged, into every teammate brief below.

**Dispatch.** For each feature in the current batch, create a worktree off `epic/<issue>`
and spawn a background teammate pinned to it (concurrency cap N, default 4). **Choose each
teammate's model tier from the feature's assessed complexity** — a cheaper/faster tier for
mechanical or well-scoped features, the strongest tier for architectural, ambiguous, or
cross-cutting ones — and pass the chosen tier's resolved model in the spawn call
(`MODEL-TIERS.md`, "Resolution at spawn time"). Fill the teammate brief below per feature.

_Liveness._ Liveness is event-driven, not polled: the harness's completion notification
— fired when a background teammate finishes or sends a message — is the primary signal,
and is acted on as it arrives rather than on a cycle. The one defined fallback: a teammate
that has produced neither a message nor a completion notification for ~30 minutes is
treated as silent. A silent or dead teammate is respawned once, into the same worktree
with the same brief. A second death on the same feature is not respawned again — mark
the feature blocked and escalate.

_Compact mode._ When Map selected the compact band, skip the per-feature fan-out above:
create one worktree off `epic/<issue>` and spawn one teammate pinned to it, briefed to
implement every feature of the band sequentially — still one branch `feat/<n>-slug` and
one PR per feature, still strict TDD per feature, and each PR is still crosschecked before
merge exactly as in Crosscheck below (the concurrency cap does not apply — there is only
one teammate). Optionally batch the review: one reviewer subagent, still spawned explicitly
at the strong-tier, reviews every PR of the band in a single strong-tier pass — checking
cross-PR consistency too — instead of one reviewer spawn per PR.

**Crosscheck (delegated).** When a teammate reports its PR, you do **not** read the diff.
Delegating the read is what keeps the orchestrator's context free for supervision as the epic
grows. Spawn one reviewer subagent per PR at the **strong-tier** — set the spawn's model
parameter to the strong-tier resolution (`MODEL-TIERS.md`, "Resolution at spawn time");
no delegation ever inherits the session model implicitly. The reviewer reads the
diff and the linked sub-issue, runs the checks below, and returns a structured verdict. The
orchestrator reads no diffs; it consumes only the verdict.

_Reviewer contract_ — the reviewer verifies, on the PR it is handed:
- **CI green** — the PR's required checks pass.
- **tests map to acceptance criteria** — every acceptance criterion of the sub-issue has a
  test that exercises it.
- **red→green evidence present** — the report or commit history shows a failing test before
  the fix.
- **scope respected** — only the files this feature needs are touched; nothing outside scope.
- **ruff clean** — `ruff check` and `ruff format --check` both pass.

_Structured verdict_ — the reviewer returns exactly this, and the same text is the PR comment
body (machine-skimmable, one check per line):
```
PR #<n>
reviewer tier: strong
verdict: PASS | FAIL
- CI green: pass | fail — <note>
- tests map to acceptance criteria: pass | fail — <note>
- red→green evidence present: pass | fail — <note>
- scope respected: pass | fail — <note>
- ruff clean: pass | fail — <note>
fixes (only on FAIL): 1. <precise fix>  2. <precise fix>  …
```
Post the verdict as a comment on the reviewed PR with `gh pr comment <n> --body …`; the verdict
text is the comment body, so each supervision decision lives on the PR, not only in the
orchestrator's context. On **PASS**, advance the PR to Merge. On **FAIL**, `SendMessage` the
same teammate the numbered fix list from the verdict and re-review once it reports back —
**max 2 fix rounds**, each driven by the next verdict. When a teammate is **stuck** — a fix
round that fails to move the verdict — send it the `/lore-workflow:debug` skill's method rather than a
vaguer "try again": reproduce, isolate the root cause, and heed its circuit breaker (after 3
failed fix attempts, stop and question the architecture instead of throwing a 4th blind fix at
the same fault). Still FAIL after the second round → mark the feature blocked and escalate.

_Tier rules for this step._ The reviewer spawn carries the strong-tier's resolved model in
its spawn call and never inherits the orchestrator's session model; the
implementation-teammate tier the Dispatch step
assigns is advisory — a deviation is allowed but recorded in the supervision trail. For the
full tier contract — ordinal/collapse, fallback, and one-step escalation — comply with
`MODEL-TIERS.md` and `CONVENTIONS.md` ("Model tiers"); record any tier escalation in the
supervision trail.

**Merge.** Merge crosscheck-passed PRs into `epic/<issue>` in dependency order; rebase later
siblings on the updated epic branch and re-run their CI. If that rebase conflicts, it goes
back to that feature's teammate — respawned with the same brief plus the conflict context if
it has already exited — since the orchestrator never resolves merge conflicts by hand;
escalate only if the teammate fails to resolve it. Cross-repo: merge a producer's feature and
capture its commit SHA before dispatching the consumer feature that pins it (e.g. a consumer
repo pins the producer library by commit SHA). **As each feature merges, close the loop on
GitHub:** tick its roadmap checkbox in the epic issue's task list (`- [ ]` → `- [x]`), close
its sub-issue (`gh issue close <n> --comment "Merged via PR #<pr>"`), and edit the status
comment to mark it merged and record the teammate's tier decision and any deviation from the
Dispatch-assigned tier.

_Post-merge invariants._ After every merge into `epic/<issue>`, verify CI is green
on the epic branch before dispatching anything that depends on it. Where the repo
carries migrations, also verify a single migration head (e.g. `alembic heads`) —
squash-merges can silently drop a re-pointed revision from a parallel feature
branch, forking the migration DAG. An invariant that fails blocks the batch: fix
forward or escalate before advancing.

**Advance.** When a batch is fully merged, dispatch the next batch. Repeat to roadmap end.

**Land.** With all features merged and epic-branch CI green, open one PR
`epic/<issue> → <detected target>` summarizing the delivered roadmap and linking every
sub-issue.

**Whole-epic review (delegated).** Before merging that PR, spawn one whole-epic
reviewer subagent, explicitly at the strong-tier, over the cumulative epic diff
(`<detected target>...epic/<issue>`). This is not a per-feature re-review — per-PR
crosscheck already covers each feature in isolation. Scope it explicitly to
cross-feature consistency: inconsistent naming, duplicated helpers, and conflicting
edits to shared files — the class of problem that only shows up once every feature's
diff lands together, which per-PR crosscheck structurally cannot see. Reuse the
Crosscheck reviewer contract and structured-verdict shape (see above), substituting
this cross-feature checklist for the per-feature one. Post the verdict as a comment
on the epic PR with `gh pr comment <n> --body …`. Its verdict gates this PR exactly
like a crosscheck: on **PASS**, proceed to merge; on **FAIL**, route the numbered fix
list to the teammate(s) owning the affected files and re-review once — **max 2 fix
rounds**, same as Crosscheck — still FAIL after the second round marks the epic
blocked and escalates rather than merging.

This epic→target merge follows the merge pre-authorization and deploy-gate policy in the
Invariants above — comply with it, and if that repo's deploy-gate marker gates this merge,
record the human confirmation in the supervision trail. Merge it. Mark the epic issue done
with the merge SHA.

**Cleanup.** Once the epic lands, delete every merged feature branch, local and
remote (`git branch -d feat/<n>-<slug>`, `git push origin --delete
feat/<n>-<slug>`), and remove its worktree (`git worktree remove`). Leave nothing
behind but the epic branch's own history.

**Document.** After the epic PR merges, invoke `document-epic` as the final automatic
stage: it reads the merged epic, classifies each changed path into the Diátaxis four,
writes or updates the relevant docs, opens a docs PR, and auto-merges it on green CI.
A human reviews post-hoc and may revert if needed. Never auto-merge on red.

**Land checklist.** Before reporting completion, emit and satisfy this checklist — every item
verified true, not merely intended:
- [ ] every roadmap checkbox ticked in the epic issue's task list
- [ ] every sub-issue closed
- [ ] the epic PR merged, with its merge SHA recorded on the epic issue
- [ ] the status comment finalized to the end state (no stale queued/running rows)
- [ ] every merged feature's branch and worktree gone, local and remote
- [ ] the docs PR opened, and merged once its CI is green

Report.

## Teammate brief (fill, pass to each agent)

> Implement **<feature title>** (sub-issue #<n>) in repo <repo>.
> Worktree <path>, branch `feat/<n>-<slug>` off `epic/<issue>`; PR targets `epic/<issue>`.
> Acceptance criteria: <criteria>.
> Context pack (shared, built once at Map time — the same text for every teammate):
> the objective, expected output format, tool/source guidance, and task boundaries,
> carrying the ranked ~1k-token code-map excerpts from `CODEMAP.md` (never the whole
> map). Read it before exploring — the repo discovery is already done; widen from
> these excerpts only as needed.
> Method: strict TDD via the `/lore-workflow:tdd` skill — failing test first, make it pass, refactor;
> include the failing-test output in the PR body. Before pushing, run ruff (check +
> format) and the full suite; both clean.
> Skip-excuses to catch yourself making — keep the discipline even when you think
> "this is just a mechanical change" (trivial edits are exactly where a typo ships),
> "I'll add tests after" (after-the-fact tests ratify the code you wrote, not the behavior
> you meant; the failing test comes first), or "the skill is overkill here" (the red→green
> loop is the floor, not ceremony reserved for hard problems). When stuck, use the `/lore-workflow:debug`
> skill's circuit breaker instead of a 4th blind fix.
> Scope fence: change only what this feature needs; no edits to shared files outside scope.
> Sibling-write hazard: parallel teammates in separate worktrees under one session
> can share an isolation pointer, so in-place editor writes (edit/write tools) from
> one teammate can clobber another's. Workaround: apply file changes via shell
> (heredoc or scripted edits) with absolute paths instead of in-place editor tools.
> Deliver: push, open the PR linking #<n>, report the PR number, red→green evidence, and a
> one-paragraph summary of changes and any decisions you made.

## Stop conditions (escalate, pause that branch only)
A feature flagged HITL (needs a human decision or design review); teammate fails crosscheck
after 2 fix rounds; ambiguous spec needing a scientific/architectural call; unresolvable merge
conflict; or CI-infra failure. Otherwise keep going. Emit the status table on every escalation and at completion.
