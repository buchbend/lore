# Conventions

The canonical conventions for the `lore-workflow` planning chain: what the
chain's steps are, where every artifact it produces lives, and the vocabulary
it uses. A repo that adopts `lore-workflow` speaks this language — PRDs, ADRs,
the agent guide, and epics all land in the same places, regardless of which
repo the chain is run in.

It answers two questions:

1. **What is the workflow chain** — the steps, bundled or not.
2. **Where does each artifact live.**

For the tier-resolution mechanics (which model a semantic tier maps to on
which host), see [`docs/model-tiers.md`](model-tiers.md); this page only fixes
which *stage* runs at which tier.

---

## The workflow chain

A piece of work flows through a fixed chain of skills. Each step hands off to
the next; the human is the checkpoint between *shaping* and *autonomous
build*.

```
seed-epic → orient → grilling → to-epic → orchestrate-epic → document-epic
```

| Step | What it does |
|------|--------------|
| `seed-epic` | End-of-session capture: distils hard-won context into a GitHub **epic seed** issue a cold session can `orient` on. A discussion seed, not a spec. |
| `orient` | First step of any work. The session does its own homework with read-only subagents, then reflects its understanding back for confirmation. Explores; does not implement or fix scope. |
| `grilling` | A relentless, one-question-at-a-time interview that stress-tests a plan. Default mode ("grill me") checks whether `domain-modeling` is needed at the end; "grill with docs" mode runs it alongside the interview from the start, sharpening terminology and updating `CONTEXT.md`/ADRs inline. |
| `to-epic` | The human checkpoint. Turns a shaped plan into an **epic tracker** issue (linking a PRD under `docs/prd/`) plus one sub-issue per feature, emitting the canonical roadmap DAG `orchestrate-epic` consumes. |
| `orchestrate-epic` | Fully autonomous: fans out one test-first teammate per feature, crosschecks every pull request, integrates onto an `epic/<n>` branch in dependency order, and lands one final pull request to the detected target branch. |
| `document-epic` | Runs as `orchestrate-epic`'s **final automatic stage** (see below), not a manual step. Updates the Diátaxis docs to match the landed epic. |
| `tdd` | The red→green→refactor loop every implementation teammate follows. |

`code-review` is a **built-in** Claude Code command, not a bundled skill —
the workflow uses it but does not ship it.

`implement-issue` is a **track beside this chain, not a step in it** — the
fast path for one well-understood issue, keeping the same discipline (strict
TDD, ADR check, Diátaxis docs pass) at single-issue weight.

**Bundled skills:** `ccat-workflow-init`, `seed-epic`, `orient`, `grilling`,
`domain-modeling`, `to-epic`, `orchestrate-epic`, `document-epic`, `tdd`,
`debug`, `implement-issue` — all
shipped as `lore-workflow:<name>` skills. `ccat-workflow-init` is a one-time
onboarding scaffold, not part of the per-epic chain above; see
[Onboard a repo](how-to/onboard-a-repo.md).

---

## Artifact homes

Every workflow artifact has exactly one canonical home:

| Artifact | Canonical home | Format |
|----------|----------------|--------|
| PRD (source of truth) | `docs/prd/NNNN-kebab.md` | MyST Markdown |
| ADR | `docs/adr/NNNN-kebab.md` | MADR-lite |
| Epic | GitHub issue (tracker) | issue body, *links* the PRD |
| Sub-issue | GitHub issue | one per feature |
| Per-repo agent guide | `AGENTS.md` (repo root) | Markdown, canonical |
| Agent guide shim | `CLAUDE.md` (repo root) | one-line `@AGENTS.md` import |

`NNNN` is zero-padded (`0001`, `0002`, ...).

**PRD stays in the repo, not the issue body.** `docs/prd/NNNN-kebab.md` is
MyST Markdown; the GitHub epic **links** the PRD rather than embedding it —
the issue body carries the roadmap DAG and sub-issue checklist, while the PRD
carries the durable spec. This keeps the spec versioned and reviewable in
PRs, rather than frozen in an issue body. `lore workflow create-prd` writes
the file and wires it into `docs/prd/index.md` idempotently — see
[`to-epic`](../lore-workflow/skills/to-epic/SKILL.md).

**ADR** lives at `docs/adr/NNNN-kebab.md` in **MADR-lite** form — Context,
Decision, Consequences / Trade-offs, Alternatives considered, plus a Status
line.

**Bidirectional cross-references** tie the chain together so any node reaches
the others:

```
PRD ↔ epic issue ↔ ADR ↔ sub-issue
```

The PRD links the epic and any ADR it depends on; the epic links the PRD and
its sub-issues; an ADR links the PRD/epic it records; each sub-issue links
back to the epic.

### Per-repo agent guide

Each repo carries one canonical agent guide, **`AGENTS.md`**, at the root —
project-specific instructions for any coding agent. **`CLAUDE.md` becomes a
one-line shim** that re-exports it:

```markdown
@AGENTS.md
```

This keeps a single source of truth (`AGENTS.md`) while staying compatible
with Claude Code's `CLAUDE.md` auto-load. `lore attach --scaffold-workflow`
(and the `ccat-workflow-init` skill, a thin pointer at the same command)
migrates an existing `CLAUDE.md` into this shape.

### gh-epic-as-tracker

The GitHub epic issue is a coordination surface, not a spec:

- a one-paragraph problem/solution summary, linking the PRD;
- the roadmap DAG (`# | Feature | Issue | Repo | Type | Blocked by`);
- a checklist of sub-issues for the parallel-batch plan.

`orchestrate-epic` reads the tracker — specifically the roadmap DAG — to
decide fan-out order. The detailed spec stays in the linked PRD; the tracker
stays the coordination surface.

### Reading the tracker with `gh`

Read epic and sub-issue bodies through the JSON API, never the rendered text
view: `gh issue view <n> --json body -q .body` (same for PR bodies and
comments). The plain-text view times out on large trackers.

---

## `document-epic`'s auto stage

`document-epic` is **not a manual step**. It runs as `orchestrate-epic`'s
final automatic subagent stage, **after the epic PR lands**:

1. The epic's feature PRs are integrated and the epic branch lands on the
   target branch.
2. `orchestrate-epic` dispatches `document-epic` as its last subagent.
3. `document-epic` generates the Diátaxis docs for the change, **opens a
   docs PR**, and **auto-merges it on green CI**.
4. A human **reviews post-hoc** — the docs land autonomously; review happens
   without blocking the merge.

This keeps documentation in lock-step with shipped code without adding a
manual gate to the autonomous loop.

### Diátaxis handoff

`document-epic` produces the Diátaxis four, and only these:

| Quadrant | What | Where |
|----------|------|-------|
| Tutorial | learning-oriented walkthrough | `docs/tutorials/` |
| How-to guide | task-oriented recipe | `docs/how-to/` |
| Reference | docstrings + code-level reference | code + `docs/` |
| Explanation | understanding-oriented background | `docs/explanation/` |

**`document-epic` NEVER touches `docs/prd/` or `docs/adr/`.** Those are the
human-owned record of intent and decisions — `to-epic` and ADR authorship
write them; `document-epic` only reads them for context.

---

## Model tiers

Every delegation point in the workflow selects a **semantic tier**
(`frontier` / `strong` / `mid` / `cheap`), never a concrete model name. The
tiers, the per-host resolution table, the ordinal/collapse rule, the
fallback behavior, and the cheap-reservation rule all live in
[`docs/model-tiers.md`](model-tiers.md) and are resolved via
`lore tier resolve <tier>`. This section fixes *which stage runs at which
tier* and *how strictly the mapping is enforced* — see also
[`lore-workflow/TIER-DELEGATION.md`](../lore-workflow/TIER-DELEGATION.md) for
the shared spawn-resolution boilerplate every skill points back to.

### Stage → tier

| Stage | Tier |
|-------|------|
| Orchestration | `frontier` |
| Grilling / synthesis | `frontier` |
| Exploration / gathering | `mid` |
| Implementation — mechanical | `mid` |
| Implementation — architectural, ambiguous, cross-cutting | `strong` |
| Crosscheck / review | `strong` |

`cheap` appears in no row: it is reserved explicitly for bulk-mechanical
sub-tasks and is **no stage's default** (see `docs/model-tiers.md`).

### Enforcement: REQUIRED vs ADVISORY

The mapping is enforced at two strengths:

- **REQUIRED** — every run: the **exploration / gathering** tier (`mid`) and
  the **crosscheck / review** tier (`strong`); and the rule that **no
  delegation ever inherits the session model implicitly** — every spawn
  names a tier *and* resolves it to a concrete model in the spawn call
  itself (see "Resolution at spawn time" in `docs/model-tiers.md`; tier
  prose alone resolves nothing). `tests/test_workflow_plugin_structural.py`
  enforces the no-hardcoded-model-name half of this mechanically.
- **ADVISORY** — the table is the default for the **implementation-teammate**
  tiers (mechanical vs. architectural/cross-cutting); a deviation is allowed
  but recorded in the supervision trail.

For the REQUIRED categories, inadequate output at a tier is retried **once**
at the next tier up (**T+1**); a second inadequate result is not escalated
again — it is raised as a blocker instead.

---

## House style for human-facing output

Artifacts written for humans — issue bodies, PR bodies, status comments,
reports — use plain, direct sentences. Expand abbreviations on first use (the
Glossary below has the full list). Prefer the standard term over the
metaphor: write "create branch", not "cut branch"; "merge into the target
branch", not "land". Every skill that writes a human-facing artifact
inherits this rule; it is not opt-in per skill.

This does not touch the workflow's own terms of art (`AFK`, `HITL`, and the
machine-read table columns) — those stay precise and unchanged so tooling
that checks them keeps working. The rule is about the surrounding prose a
human reads.

## Glossary

Plain-English definitions of the workflow's terms of art. The terms
themselves stay — they are precise and machine-checked (the roadmap
validator reads the literal `AFK`/`HITL` tokens, for example) — these
definitions just remove the decoding cost for a reader meeting them for the
first time.

- **tracer bullet** — a thin slice of a feature, built end-to-end, that
  proves the approach works before the feature is built out further.
- **vertical slice** — a piece of work that cuts through every layer (data,
  logic, UI) instead of one layer at a time.
- **AFK** — "away from keyboard": the feature runs autonomously, no human
  input needed.
- **HITL** — "human-in-the-loop": the feature needs a human decision or
  design review.
- **crosscheck** — the strong-tier review a delegated reviewer performs on a
  teammate's PR before merge.
- **land** — merge a pull request into its target branch.
- **cut** (a branch) — create a new branch.
- **fan out** — dispatch multiple teammate agents to work in parallel, one
  per feature.
- **green / red** — a test suite that passes / fails; shorthand for the
  TDD loop.
- **deploy gate** — a marker a repo declares that requires human
  confirmation before a merge that would deploy, instead of merging
  automatically.
- **supervision trail** — the durable, on-GitHub record of what an
  autonomous run did: status comments, crosscheck verdicts, tier
  escalations.
- **epic seed** — a tracker issue capturing a session's context for a cold
  session to `orient` on; not yet a formed spec.
- **cold session** — a fresh session with no memory of prior conversation,
  starting only from the repo and issue it is pointed at.
