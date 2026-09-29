---
name: lore-workflow:super-orchestrate
description: Supervise several epic tracker issues in one run — map the dependencies between the
  epics, then hand each epic to its own epic-lead subagent running orchestrate-epic, starting an epic
  once its upstream epics have merged. Every spawn runs at the frontier tier unless told otherwise.
  Use when the user hands over two or more epics for autonomous implementation.
---

# Super Orchestrate

You are the **supervisor**: you map the epics, order them, dispatch one epic lead per epic, and relay
escalations to the human. Each epic lead runs [`orchestrate-epic`](../orchestrate-epic/SKILL.md). You
write no code, merge no PR, and read no diff.

**Input:** two or more epic tracker issues (numbers or URLs), possibly spanning repos. Optional, stated in
the invocation: a tier floor, `strong` or `frontier` (default `frontier`), and a concurrent-epic cap M
(default 3). A floor below `strong` would drop the review tier `orchestrate-epic` requires.
**Mode:** one human confirmation of the epic list and order (see Confirm), then fully autonomous, as
`orchestrate-epic`. After that, stop only to report completion or to put an escalation to the human.

## Invariants
- One epic = one epic lead = one `orchestrate-epic` run = one `epic/<issue>` branch = one epic PR.
- **The epic is the unit of ordering.** A downstream epic starts only after every upstream epic's PR has
  merged into its target branch. No feature of one epic waits on a single feature of another.
- **Overlap is no dependency.** Epics that only touch the same files or add migrations in one repo run side
  by side. The lead whose epic merges second reconciles both in its fix step (see Lead brief).
- **Tier floor.** Every spawn in the run — the analyst, each epic lead, and every teammate, reviewer and
  docs pass under a lead — resolves `lore tier resolve <floor>` and passes the result as the spawn's
  model. The default floor is `frontier`. No spawn inherits a model
  ([TIER-DELEGATION.md](../../TIER-DELEGATION.md)).
- **Each epic keeps its own board.** You never edit an `orchestrate-epic` board comment. Your one record per
  epic is the upstream comment (see Record), edited in place.
- **You talk to the human; epic leads never do.** A lead that needs a human decision reports to you.

## Loop

**Gate.** Per epic: `gh issue view <n> -R <owner/repo> --json body -q .body | lore workflow
validate-roadmap --json -`. Any `ok: false` → report every failing epic's `problems` and stop before any
spawn. A partial run on a known-bad input only moves the failure downstream.

**Resume.** Look for the upstream comment (marker below) on every epic.
- Present on every epic, and every epic the comments name is in the input → the epic DAG is on record and
  confirmed: skip Map, Confirm and Record. Read the tier floor back from the comments.
- Otherwise the input changed: Map all epics again.
- Per epic, derive state from GitHub. Every epic PR merged into its target branch → **merged**; read each
  merge SHA with `gh pr view <pr> --json mergeCommit`. An `orchestrate-epic` board comment, with every upstream
  epic merged → **started**: its lead resumes from that board. Anything else → **queued**.

**Map (delegated).** Spawn one analyst subagent at the tier floor. Per epic it reads the body and roadmap,
the linked PRD under `docs/prd/`, the sub-issues' acceptance criteria, and ranked `lore codemap` slices
of the epic's touchpoints. It returns only edge blocks, then overlap lines, then one `independent:` line:
```
edge: owner/repo#<upstream> -> owner/repo#<downstream>
kind: explicit | interface | pin
evidence: <issue ref, file path or symbol> — <one line>

overlap: owner/repo#<a> <-> owner/repo#<b> — <shared files, symbols or migrations>

independent: owner/repo#<n>, …
```
Edge kinds:
- **explicit** — an epic body, PRD or sub-issue names another input epic, or one of its sub-issues, as a
  blocker. GitHub issue dependencies count too
  (`gh api repos/<owner>/<repo>/issues/<n>/dependencies/blocked_by`).
- **interface** — the downstream epic consumes an API, schema, CLI or module the upstream epic adds or
  changes.
- **pin** — a downstream repo pins a commit or release that the upstream epic produces.

An **overlap** is no edge: both epics edit the same files or symbols, or both add migrations, in one repo.
Two epics without an edge run side by side, overlap or not.

**Order.** Build the epic DAG from the edges. A cycle → report the cycle with each edge's evidence and stop.
Only the human can split or merge the epics that form it.

**Confirm.** The run's one human gate, before any comment or lead. Show one table: epic ref, title, repo,
upstream epics, overlaps. Ask one question: are these the intended epics, in this order? A wrong epic
number shows up here as a wrong title. On a correction, re-run Gate and Map for the corrected list.

**Record.** On each epic, post one comment, or edit the existing one in place:
```
<!-- lore-super-orchestrate:upstream v1 -->
Upstream epics: owner/repo#<n> (<kind>) — <evidence> | none
Overlaps: owner/repo#<n> — <shared files, symbols or migrations> | none
Tier floor: <floor>
```

**Dispatch.** At most M leads run at once. Whenever a slot is free — at the start and after every lead
report — launch the next lead: started epics first, then queued epics whose upstream epics have all merged.
Each lead is a background subagent at the tier floor, spawned with worktree isolation and
`LORE_SUPPRESS_CAPTURE=1` in its environment. Pass it the filled Lead brief below.

_Liveness._ Event-driven: a lead's completion notification is the signal; never poll. A lead that ends
without a `merged` or `escalation` report is respawned once with the same brief. `orchestrate-epic`
resumes it from its board. A second death marks the epic blocked; escalate.

**On each lead report.**
- **merged** — note each repo's epic PR and merge SHA, and pass them to the downstream leads. `SendMessage`
  every running lead that overlaps the merged epic: its target branch moved, so it repeats its fix step.
- **escalation** — any `orchestrate-epic` stop condition, or a deploy-gate confirmation. Ask the human one
  question per escalation, with the lead's evidence attached. The answer either resumes the lead by
  `SendMessage`, or blocks the epic. Other epics keep running.
- A **blocked** epic — blocked by the human or by a second death — keeps its downstream epics queued;
  report them with the cause.

**Final checklist.** Before reporting completion, emit and satisfy this — each item verified true, not
merely intended:
- [ ] every epic is merged, or blocked with its cause and its queued downstream epics listed
- [ ] every merged epic's board is finalized, with no queued or running rows
- [ ] every upstream comment matches the edges this run acted on
- [ ] no epic lead is still running

**Follow-ups.** Work that surfaced between epics — an interface gap, a missing epic — goes through
[`file-issue`](../file-issue/SKILL.md), never inline.

Report, per epic: state, epic PR, merge SHA, upstream edges.

## Lead brief (fill, pass to each epic lead)

> You are the epic lead for owner/repo#<n>. Run `/lore-workflow:orchestrate-epic owner/repo#<n>` to
> completion.
> Tier floor: `<floor>`. Every spawn you make — teammates, reviewers, `document-epic` — passes the model
> `lore tier resolve <floor>` returns. The floor replaces the tier choices `orchestrate-epic` makes; record
> it once in the board's notes section.
> Upstream epics, merged: owner/repo#<u> at <sha> (<kind>: <evidence>) | none. Build on them; never
> re-implement them.
> Overlapping epics, running beside you: owner/repo#<m> — <shared files, symbols or migrations> | none.
> Read their bodies and PRDs, so you know what each one intends.
> **Fix step**, after the last feature merges and before the epic tail. Merge the target branch into
> `epic/<n>`. Resolve every conflict yourself, keeping the intent of both epics. You are the one agent
> that knows why each side changed. Re-parent this epic's migrations onto the target branch's head and
> verify a single head. Get the full suite and epic-branch CI green. Repeat the fix step whenever the
> target branch moves before your epic PR merges, or when the supervisor says it moved. A fix step that
> resolved a conflict goes to the whole-epic reviewer again, even for two features or fewer, even after a
> passing review. A deploy-gate confirmation given for the earlier tree is asked again. The fix step overrides
> `orchestrate-epic`'s rule against resolving conflicts by hand, for this merge only. A conflict between
> features of your own epic still goes back to their teammate.
> You run in your own worktree. Never switch the branch of a checkout you did not create. Other leads run
> beside you under one session, and in-place editor writes (Edit/Write) can land in a sibling's worktree.
> Apply your own file changes via shell at absolute paths.
> You cannot reach the human. Every stop `orchestrate-epic` would escalate or mark blocked, and every
> deploy-gate confirmation, is an escalation to the supervisor. Let the other features of the epic reach
> merged or blocked first. Then end with an escalation report: the question, the evidence, the board
> state. The supervisor answers by message, and you resume.
> End with exactly one report: `merged` (per repo: epic PR, merge SHA) | `escalation` (as above).

## Stop conditions
A roadmap failing the gate; a cycle in the epic DAG; the confirmation pending or rejected; an escalation
only the human can answer (that epic pauses, the others run on); a lead dying twice. Otherwise keep going.
