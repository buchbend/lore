---
name: lore-workflow:orchestrate-epic
description: Supervise parallel TDD implementation of an epic tracker issue across one or
  more repos — plans, dispatches teammates, crosschecks every PR, and lands the epic
  autonomously. Use when the user points at an epic/tracker issue and wants orchestrated,
  batched, autonomous implementation.
---

# Orchestrate Epic

You are the **orchestrator**: you plan, dispatch, crosscheck, integrate. Teammate agents write the feature
code; you do not.

**Input:** an epic tracker issue (number or URL), possibly spanning repos.
**Mode:** fully autonomous — run the whole loop without asking. Stop only to report completion or to escalate
a hard blocker (see Stop conditions).

## Invariants
- **Repo facts come from `lore workflow`, never re-derived in prose.** Resolve target branch and deploy gate
  once at Map time with `lore workflow epic-policy <repo_root>` → `{target_branch, deploy_gate}`.
  `epic/<issue>` is cut from `target_branch` and lands back on it via one final PR; feature PRs target the
  epic branch.
- **Merges are pre-authorized** — you merge on a passing crosscheck, never asking. Sole exception: when
  `epic-policy` returns `deploy_gate: true`, the final epic→target merge (only that one) requires one human
  confirmation first.
- **Supervision state splits into two durable stores (ADR 0002), never conflated.** The **board** — one
  comment on the epic issue, the per-feature ledger (Feature/Issue/Tier/Batch/State/PR), machine-readable via
  `lore workflow parse-board`, kept on GitHub for humans and teammates; authoritative for per-feature state.
  The **epic note** — your own session note, linkage `epics: [<issue>]`, composed by capture as you narrate;
  it holds what the board can't: tier rationale, dispatch and crosscheck reasoning, escalations, in-flight
  decisions. You write only your OWN epic note (ADR 0001 forbids writing another session's); dispatched
  teammates run capture-suppressed, so the epic note is the single consolidated record.
- One feature = one teammate = one worktree = one branch `feat/<sub-issue>-slug` = one PR.
- **Compact mode narrows fan-out only** (see Effort bands) — the strict-TDD, one-PR-per-feature, and
  every-PR-crosschecked invariants all still hold.
- Strict TDD (red→green→refactor) per feature — no green PR without tests mapping its acceptance criteria.
  Never merge on red CI.

## Loop

**Map.** Read the epic (`gh ... --json`): roadmap items, per-feature acceptance criteria, dependency edges,
target repo(s). If it came from `/lore-workflow:to-epic`, its **Roadmap** table (Feature | Issue | Repo |
Type | Blocked by) is the canonical DAG, and HITL-flagged features are escalation points, not
auto-implemented. Resolve `lore workflow epic-policy` once per repo.

**Resume.** The entry point — reached before the gate, before cutting the epic branch. Two reads recover a
prior run:
1. **Board** — fetch the epic issue's board comment and pipe it to `lore workflow parse-board` → rows
   `{feature, issue, tier, batch, state, pr}`. Never scrape it with the model. Reconcile against the epic
   branch's real state: a `merged` row is done (skip, never redispatch); a `queued`/`blocked` row is
   redispatched; an existing epic branch is reused, never re-cut. Edit that same comment in place for every
   later update; never open a second.
2. **Working context** — `lore_resume` / `lore_context_pack` (epic-linked) surface prior orchestrator epic
   notes: why a feature blocked, the tier rationale, the in-flight decisions the board omits.

No board comment found → fresh run; proceed to the gate.

**Roadmap gate + effort band.** Run `lore workflow validate-roadmap --json` →
`{ok, rows, repos, edges, problems}`. `ok: false` → refuse to start: report `problems` and stop, never
dispatching against a malformed or cyclic roadmap. Otherwise pick the band from the counts, not by eye:
**compact** iff `rows ≤ 2` and `repos == 1` and every `Type` is AFK with no HITL flags; **standard** otherwise
(full batched loop, concurrency cap N). Record the band in your epic note. Then build the feature DAG, split
into dependency-ordered batches (features in a batch are independent), and cut and push `epic/<issue>` from
the up-to-date `target_branch`.

**Emit the board.** One comment on the epic issue, edited in place at a few deliberate points (batch start,
each merge, each blocker, completion) — never a second comment. It MUST carry, verbatim, the marker and
columns `parse-board` reads:
```
<!-- lore-orchestrate-epic:status v1 -->

| Feature | Issue | Tier | Batch | State | PR |
|---|---|---|---|---|---|
| <title> | owner/repo#n | <tier> | <batch> | queued | #<pr> or — |
```
`State` is one of queued/running/crosscheck/merged/blocked. The board is the per-feature ledger only; tier
*rationale* and deviations belong in your epic note.

**Codemap excerpt (built once at Map time, reused by every teammate).** So codebase discovery happens once for
the whole epic, not once per teammate: from `lore codemap` (or the `lore_codemap` MCP tool for bounded
slices), rank the map's symbols against this epic's shared touchpoints and pull only the top **~1k-token
excerpts** — never the whole map. Shape it as the four-part subagent brief every teammate reads unchanged:
- **Objective** — the epic's shared goal and how each feature slices through it.
- **Expected output format** — one branch `feat/<n>-slug`, one PR per feature, red→green TDD evidence.
- **Tool/source guidance** — the ranked codemap excerpts (files, symbols, conventions), `lore codemap` /
  `lore_codemap` to widen from, where domain language lives (CONTEXT.md / glossary, `docs/adr`).
- **Task boundaries** — the scope fence and shared-file touchpoints to stay clear of.

**Dispatch.** Per feature in the batch: worktree off `epic/<issue>`, spawn a background teammate pinned to it
(cap N, default 4) **with `LORE_SUPPRESS_CAPTURE=1` in its environment**, so it lands its work in your epic
note rather than scattering a standalone fragment. Choose the model tier from the feature's assessed
complexity (cheaper for well-scoped work, strongest for cross-cutting) and pass the resolved model in the
spawn call (`lore tier resolve <tier>`, see [TIER-DELEGATION.md](../../TIER-DELEGATION.md)); no delegation
inherits your session model. Record the tier and its rationale in your epic note; the board carries only the
assigned tier.

_Liveness._ Event-driven, not polled: the harness's completion notification is the primary signal. Fallback:
a teammate silent ~30 minutes is respawned once into the same worktree with the same brief; a second death on
the same feature is not respawned — mark it blocked and escalate.

_Compact mode._ Skip the fan-out: one worktree, one teammate, briefed to implement every feature of the band
sequentially — still one branch and one PR per feature, still strict TDD, still each PR crosschecked below.
Optionally batch the review: one strong-tier reviewer over the band's PRs in a single pass.

**Crosscheck (delegated).** When a teammate reports its PR you do **not** read the diff — delegating the read
keeps your context free as the epic grows. Spawn one reviewer subagent per PR at the **strong-tier**
(`lore tier resolve strong`; no delegation inherits the session model). It reads the diff and the linked
sub-issue and returns exactly this verdict, one line per check, posting the same text as the PR comment; you
consume only the verdict and record its outcome in your epic note:
```
PR #<n>
reviewer tier: strong
verdict: PASS | FAIL
- CI green: pass | fail — <note>
- tests map to acceptance criteria: pass | fail — <note>
- red→green evidence present (failing test first): pass | fail — <note>
- scope respected (only files this feature needs): pass | fail — <note>
- ruff clean (check + format --check): pass | fail — <note>
fixes (only on FAIL): 1. <precise fix>  2. <precise fix>  …
```
Post with `gh pr comment <n> --body …`. On **PASS**, advance to Merge. On **FAIL**, `SendMessage` the teammate
the numbered fix list and re-review once it reports back — **max 2 fix rounds**. A round that doesn't move the
verdict → send the `/lore-workflow:debug` method (reproduce, isolate root cause, heed its circuit breaker),
not a vaguer "try again". Still FAIL after the second → mark the feature blocked and escalate. The Dispatch
tier is advisory; a reviewer tier deviation is allowed but recorded in your epic note (full contract:
[TIER-DELEGATION.md](../../TIER-DELEGATION.md), `docs/model-tiers.md`).

**Merge.** Merge crosscheck-passed PRs into `epic/<issue>` in dependency order; rebase later siblings on the
updated branch and re-run their CI. A rebase conflict returns to that feature's teammate (respawned with
conflict context if it exited) — you never resolve conflicts by hand; escalate only if the teammate can't.
Cross-repo: merge a producer's feature and capture its commit SHA before dispatching the consumer that pins
it. As each feature merges, close the loop: tick its roadmap checkbox (`- [ ]` → `- [x]`), close its sub-issue
(`gh issue close <n> --comment "Merged via PR #<pr>"`), set its board row to `merged`, and record the outcome
and any tier deviation in your epic note. When a batch is fully merged, dispatch the next; repeat to the
roadmap's end.

_Post-merge invariants._ After every merge into `epic/<issue>`, verify epic-branch CI is green before
dispatching dependents. Where the repo carries migrations, verify a single migration head (e.g. `alembic
heads`) — squash-merges can silently fork the migration DAG. A failed invariant blocks the batch: fix forward
or escalate.

**Land.** All features merged and epic-branch CI green → open one PR `epic/<issue> → <target_branch>` linking
every sub-issue.

**Whole-epic review (delegated).** Before merging that PR, spawn one strong-tier reviewer over the cumulative
diff (`<target_branch>...epic/<issue>`). Not a per-feature re-review — scope it to cross-feature consistency
(inconsistent naming, duplicated helpers, conflicting edits to shared files), the class that only surfaces
once every diff lands together. Reuse the reviewer verdict shape with this cross-feature checklist; post it on
the epic PR. It gates the merge like a crosscheck: **PASS** → merge; **FAIL** → route the fix list to the
teammate(s) owning the affected files, re-review once, max 2 rounds, then mark the epic blocked and escalate
rather than merge. The epic→target merge follows the deploy-gate policy: if `epic-policy` returned
`deploy_gate: true`, record the one human confirmation in your epic note before merging. Merge, and mark the
epic issue done with the merge SHA.

**Cleanup.** Delete every merged feature branch (local and remote) and remove its worktree. Leave nothing
behind but the epic branch's own history.

**Document.** Invoke `document-epic` as the final automatic stage: it classifies changed paths into the
Diátaxis four, opens a docs PR, and auto-merges on green CI (never on red).

**Land checklist.** Before reporting completion, emit and satisfy this — each item verified true, not merely
intended:
- [ ] every roadmap checkbox ticked and every sub-issue closed
- [ ] the epic PR merged, its merge SHA recorded on the epic issue
- [ ] the board finalized to the end state (no stale queued/running rows)
- [ ] every merged feature's branch and worktree gone, local and remote
- [ ] the docs PR opened, and merged once its CI is green

Report.

## Teammate brief (fill, pass to each agent)

> Implement **<feature title>** (sub-issue #<n>) in repo <repo>.
> Worktree <path>, branch `feat/<n>-<slug>` off `epic/<issue>`; PR targets `epic/<issue>`.
> Acceptance criteria: <criteria>.
> Codemap excerpt (shared, same text every teammate): objective, expected output format, tool/source
> guidance, task boundaries, carrying the ranked ~1k-token codemap excerpts from `lore codemap` (never the
> whole map). Read it before exploring — repo discovery is done; widen from it only as needed.
> Method: strict TDD via `/lore-workflow:tdd` — failing test first, make it pass, refactor; include the
> failing-test output in the PR body. Before pushing, run ruff (check + format) and the full suite, both
> clean. When stuck, use the `/lore-workflow:debug` circuit breaker, not a 4th blind fix.
> Scope fence: change only what this feature needs; no edits to shared files outside scope.
> Sibling-write hazard: parallel teammates in separate worktrees under one session can share an isolation
> pointer, so in-place editor writes (Edit/Write) from one can clobber another. Apply file changes via shell
> (heredoc or scripted edits) at absolute paths instead.
> Deliver: push, open the PR linking #<n>, report the PR number, red→green evidence, and a one-paragraph
> summary of changes and any decisions you made.

## Stop conditions (escalate, pause that branch only)
A feature flagged HITL; a teammate failing crosscheck after 2 fix rounds; an ambiguous spec needing a
scientific/architectural call; an unresolvable merge conflict; or a CI-infra failure. Otherwise keep going.
Emit the board on every escalation and at completion.
