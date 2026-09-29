# Conventions

The conventions of the `lore-workflow` chain: the steps, where each fact
lives, which stage runs at which tier, and the terms the chain uses. A repo
that adopts `lore-workflow` speaks this language.

For the tier-resolution mechanics (which model a semantic tier maps to on
which host), see [`docs/model-tiers.md`](model-tiers.md). This page only names
the tier of each stage.

---

## The workflow chain

A piece of work flows through a chain of skills. The human is the checkpoint
between shaping and the autonomous build.

```
orient → grilling → to-epic → build → document
```

`handover` runs at any stop.

| Step | What it does |
|------|--------------|
| `orient` | The first step. The session does its own homework, then reflects its understanding back for confirmation. A light mode ("brief me") pulls only the context pack and hands one change to `build`. |
| `grilling` | An interview that stress-tests a plan. It also sharpens the domain model: it writes approved terms into `CONTEXT.md` and ADRs with `Holds` and `Revisit if`. |
| `to-epic` | The human checkpoint. Writes the PRD under `docs/prd/`, the epic tracker issue and one sub-issue per feature, with the roadmap table `build` reads. |
| `build` | Builds code in one of three modes. See below. |
| `document` | Brings the Diátaxis docs in line with the code. At the epic tail it runs before the merge and marks the PRD shipped. |
| `handover` | Writes the handover section for work that stops before its finish point, and turns follow-up work into a seed issue. |
| `tdd` | The red-green-refactor loop every `build` mode follows. |
| `debug` | Root-cause debugging with a circuit breaker after three failed fixes. |
| `file-issue` | Writes issue text and PR bodies under the writing rules and files them. |

`code-review` is a built-in Claude Code command, not a bundled skill. The
workflow uses it but does not ship it.

### Build modes

| Mode | Use when | Finish point |
|------|----------|--------------|
| `loop` | The user reacts to a running feature in fast rounds. | One wrap-up PR from `loop/<slug>`. Rounds merge locally; nothing else reaches the remote. |
| `issue` | One written, clear issue. | One PR. The user merges it. |
| `epic` | An epic tracker issue with a roadmap. | The epic PR, then the handover section in the epic issue. |

Every mode keeps a ledger of ADR candidates, term candidates and
left-on-the-table items. `lore workflow ledger-check` blocks each finish
point while a line is still open. After each merged round or feature the
agent writes a resume line, and `/clear` is then safe. See
[the ledger reference](architecture/ledger.md).

Several epics take one `build` run each. Start a downstream epic after its
upstream epic merged.

Onboard a repo once with `lore attach --scaffold-workflow`. See
[Onboard a repo](how-to/onboard-a-repo.md).

---

## Where each fact lives

ADR 0014 holds the table of homes: which artifact holds each kind of fact,
and who writes it. See
[ADR 0014 § Decision](adr/0014-one-home-per-fact.md#decision). Other
artifacts link to the home and do not restate the fact.

Placement details the table leaves out:

- A PRD is `docs/prd/NNNN-kebab.md` in MyST Markdown. `lore workflow
  create-prd` writes the file and wires it into `docs/prd/index.md`. `NNNN`
  is zero-padded (`0001`, `0002`).
- An ADR is `docs/adr/NNNN-kebab.md` in the format of the ADR template in the
  `grilling` skill.
- The epic issue links the PRD and does not embed it. See
  [Why the PRD lives in the repo](explanation/why-prd-in-repo.md).
- Cross-references run both ways: PRD, epic issue, ADR and sub-issue each
  link the others.

### Per-repo agent guide

Each repo carries one agent guide, **`AGENTS.md`**, at the root. `CLAUDE.md`
is a one-line shim that imports it:

```markdown
@AGENTS.md
```

Claude Code then loads `AGENTS.md` through `CLAUDE.md`. `lore attach
--scaffold-workflow` moves an existing `CLAUDE.md` into this shape.

### The epic issue as tracker

The GitHub epic issue coordinates the work. It is not the spec:

- a one-paragraph summary that links the PRD;
- the roadmap table (`# | Feature | Issue | Repo | Type | Blocked by`);
- a checklist of the sub-issues.

`build` reads the roadmap table to order the features. At the finish point
it writes the handover section into the epic issue body.

### Reading the tracker with `gh`

Read issue and PR bodies through the JSON API, not the rendered text view:
`gh issue view <n> --json body -q .body`. The plain-text view times out on
large trackers.

---

## Docs at the epic tail

`document` runs inside the `build` epic tail, before the epic PR merges
(ADR 0005):

1. Every feature PR merged into `epic/<n>`, and its CI is green.
2. `document` in pre-merge mode updates the Diátaxis docs for the whole
   diff. It commits them onto the epic branch. No separate docs PR.
3. `document` runs `lore workflow prd-ship` to set the PRD's status to
   `shipped`.
4. For an epic with three or more features, the whole-epic reviewer checks
   the docs commit against the behaviour.
5. When the docs step fails, the epic merges without it. `document` then
   runs in catch-up mode and opens its own docs PR.

`document` produces the Diátaxis four:

| Quadrant | What | Where |
|----------|------|-------|
| Tutorial | learning-oriented walkthrough | `docs/tutorials/` |
| How-to guide | task-oriented recipe | `docs/how-to/` |
| Reference | docstrings and code-level reference | code and `docs/` |
| Explanation | understanding-oriented background | `docs/explanation/` |

`document` does not edit the content of `docs/prd/` or `docs/adr/`. The one
exception is the status change `prd-ship` makes.

---

## Model tiers

Every delegation point names a semantic tier (`frontier`, `strong`, `mid`,
`cheap`), never a concrete model. `lore tier resolve <tier>` resolves it. The
rules for each tier live in [`docs/model-tiers.md`](model-tiers.md). The spawn
rules every skill shares live in
[`lore-workflow/TIER-DELEGATION.md`](../lore-workflow/TIER-DELEGATION.md).

### Stage and tier

| Stage | Tier |
|-------|------|
| `build` epic orchestration | `frontier` |
| `grilling`, `handover` | `frontier`, in the main session |
| Exploration in `orient` | `mid` |
| Implementation, mechanical | `mid` |
| Implementation, architectural or cross-cutting | `strong` |
| Review of a PR at risk level `low` | `mid` |
| Review of a PR at risk level `high` | `strong` |
| Loop wrap-up advisory pass | `strong` |
| Loop background suite | `cheap` |

`lore workflow risk <pr>` sets the risk level from the diff. An agent may
raise the level and does not lower it. CI and ruff status come from `gh pr
checks`, not from the reviewer.

### Required and advisory

- **Required:** the exploration tier, the review tier that the risk level
  sets, and the rule that no spawn inherits the session model. Every spawn
  names a tier and passes the resolved model in the spawn call.
  `tests/test_workflow_plugin_structural.py` checks that no skill names a
  concrete model.
- **Advisory:** the implementation-teammate tiers. A deviation is allowed.

---

## House style for human-facing output

The writing rules hold the prose rules for issue bodies, PR bodies, ADRs,
PRDs, docs and the handover section. Run `lore style show writing-rules` to
print the version for the current repo. Every skill that writes team-facing
text runs the command first.

The workflow's terms of art (`AFK`, `HITL`, the machine-read table columns)
stay as they are. Tools read them.

## Glossary

Plain definitions of the workflow's terms of art. Domain terms live in the
repo's `CONTEXT.md`.

- **tracer bullet** — a thin slice of a feature, built end to end, that
  proves the approach before the feature grows.
- **vertical slice** — a piece of work that cuts through every layer (data,
  logic, UI) instead of one layer at a time.
- **AFK** — "away from keyboard": the feature runs without human input.
- **HITL** — "human in the loop": the feature needs a human decision or a
  design review.
- **review** — the check a delegated reviewer runs on a PR before the merge:
  correctness, acceptance criteria, scope.
- **fan out** — dispatch several teammate agents in parallel, one per
  feature.
- **green / red** — a test suite that passes or fails.
- **deploy gate** — a marker in a repo that asks for human confirmation
  before a merge that deploys.
- **board** — the one status comment on an epic issue: the feature table,
  the ledger section and the notes on blockers and escalations.
- **seed issue** — a tracker issue that `handover` writes for a cold
  session to `orient` on. It is not a spec yet. Label: `epic-seed`.
- **cold session** — a fresh session with no memory of an earlier
  conversation. It starts only from the repo and the issue it gets.
