# Why the PRD lives in the repo

The **product requirements document** (PRD) is the source of truth for what
an epic sets out to do, and it lives in the repo at `docs/prd/NNNN-kebab.md`
as Markdown — *not* embedded in a GitHub issue. The GitHub epic issue is a
**tracker** that links the PRD. This page explains why the spec lives in the
repo and the issue stays a coordination surface.

## The problem it solves

A specification frozen in a GitHub issue body is hard to live with. It is
not versioned alongside the code it describes, so the two drift apart with
no diff to show it. It is not reviewable in a pull request, so a change to
the spec gets no review.

Putting the PRD in the repo fixes both at once:

- **It is versioned with the code.** The spec and the implementation move
  through the same history; a change to intent is a commit, visible in
  `git log`.
- **It is reviewable.** A PRD change arrives as a pull request diff and gets
  the same review as any other change.

## The tracker-versus-spec split

The division of labour is deliberate:

- The **PRD** (in the repo) carries the durable spec — the problem, the
  solution, the requirements.
- The **epic issue** (on GitHub) carries the coordination surface — a
  one-paragraph summary and a link to the PRD, the roadmap dependency table,
  and the sub-issue checklist. `build` reads this tracker to decide the
  order of the features; it does not need the full spec to schedule the
  work.

This keeps each artifact doing the one thing it is good at: GitHub is good
at tracking state across issues and pull requests; a versioned repo file is
good at holding a reviewable specification.

## Why `document` leaves it alone

The PRD and the ADRs (`docs/adr/`) are the **human-owned record of intent
and decisions**. The `document` pass writes only the Diátaxis docs that
describe the shipped result. It reads the PRD and ADRs for context and does
not write, rename or delete them. One change is allowed: at the epic tail,
`lore workflow prd-ship` sets the PRD's status to `shipped` and names the
current ADRs.
Keeping the source of truth out of the autonomous writer's reach is what
lets the docs be generated autonomously without ever putting the spec at
risk.

[ADR 0014](../adr/0014-one-home-per-fact.md#decision) names the home of
each kind of fact. [`docs/conventions.md`](../conventions.md) ("Where each
fact lives") names the links between the PRD, the epic, the ADRs and the
sub-issues.
