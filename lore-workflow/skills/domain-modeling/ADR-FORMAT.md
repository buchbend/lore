<!--
Adapted for CCAT: MADR-lite template (Context · Decision · Consequences /
Trade-offs · Alternatives considered · Status) plus a deterministic toctree-
wiring step. Deviates from upstream mattpocock/skills, whose ADR-FORMAT.md uses
a freeform 1-3 sentence template with optional sections and no docs-site wiring.
The three-criteria "offer ADRs sparingly" gate is kept verbatim from upstream.
See THIRD-PARTY.md for provenance.
-->

# ADR Format

ADRs live in `docs/adr/` and use sequential numbering: `0001-slug.md`,
`0002-slug.md`, etc. The slug is kebab-case.

Create the `docs/adr/` directory lazily — only when the first ADR is needed.

## Template

CCAT uses **MADR-lite**: a fixed set of sections so every ADR reads the same way
and renders predictably in the docs site. Fill them all — terse is fine, but an
ADR is more than a single paragraph here.

```md
# NNNN — {Short title of the decision}

- **Status:** {Proposed | Accepted | Deprecated | Superseded by ADR-NNNN}
- **Date:** {YYYY-MM-DD}
- **Deciders:** {who}
- **Relates to:** {links to the PRD / epic / sub-issue this records}

## Context

What forces are in play — the problem, the constraints, the background a future
reader needs.

## Decision

What we decided, stated plainly.

## Consequences / Trade-offs

What becomes easier and what becomes harder as a result. Call out the costs, not
just the wins.

## Alternatives considered

The genuine alternatives and why each was rejected. The non-obvious rejections
are the valuable part — they stop someone re-proposing a settled option later.

## Status

(Also carried in the header line above.) Track the decision's lifecycle:
`Proposed` → `Accepted`, and later `Deprecated` or `Superseded by ADR-NNNN` when
revisited.
```

## Numbering

Scan `docs/adr/` for the highest existing `NNNN` and increment by one. Zero-pad
to four digits.

## Wire the ADR into the docs site

Writing the ADR body is your job; **wiring it into the toctree is a deterministic
step you must always do.** After creating `docs/adr/NNNN-slug.md`, add its
stem (`NNNN-slug`, no `.md` extension) to the **first `{toctree}` block** in
`docs/adr/index.md` — the single-brace MyST stub already seeded in the repo.
Insert the entry on its own line **before the closing `` ``` `` fence** of that
block, preserving the existing entries. This mirrors the `_wire_toctree_entry()`
convention in `scripts/ccat_workflow_init.py`; it is idempotent, so skip the
insert if the stem is already present. Without this wiring Sphinx will not pick
the ADR up and it will not render.

## When to offer an ADR

ADRs are offered **sparingly**. All three of these must be true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will look at the code and wonder "why on earth did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If a decision is easy to reverse, skip it — you'll just reverse it. If it's not surprising, nobody will wonder why. If there was no real alternative, there's nothing to record beyond "we did the obvious thing."

### What qualifies

- **Architectural shape.** "We're using a monorepo." "The write model is event-sourced, the read model is projected into Postgres."
- **Integration patterns between contexts.** "Ordering and Billing communicate via domain events, not synchronous HTTP."
- **Technology choices that carry lock-in.** Database, message bus, auth provider, deployment target. Not every library — just the ones that would take a quarter to swap out.
- **Boundary and scope decisions.** "Customer data is owned by the Customer context; other contexts reference it by ID only." The explicit no-s are as valuable as the yes-s.
- **Deliberate deviations from the obvious path.** "We're using manual SQL instead of an ORM because X." Anything where a reasonable reader would assume the opposite. These stop the next engineer from "fixing" something that was deliberate.
- **Constraints not visible in the code.** "We can't use AWS because of compliance requirements." "Response times must be under 200ms because of the partner API contract."
- **Rejected alternatives when the rejection is non-obvious.** If you considered GraphQL and picked REST for subtle reasons, record it — otherwise someone will suggest GraphQL again in six months.
