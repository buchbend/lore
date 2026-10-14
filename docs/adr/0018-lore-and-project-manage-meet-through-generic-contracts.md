# ADR 0018: Lore and project-manage meet only through generic contracts

- **Status:** Accepted
- **Date:** 2026-10-14
- **Deciders:** Christof Buchbender
- **Relates to:**
  - PRD [0018](../prd/0018-the-roadmap.md) (the roadmap), epic [#510](https://github.com/buchbend/lore/issues/510)
  - ADR [0017](0017-the-logbook.md) (the logbook, which rejected a built-in project-manage sink)
  - ADR [0013](0013-search-over-wiki-and-github-is-federated-not-cached.md) (federated search)
  - ADR [0014](0014-one-home-per-fact.md) (one home per fact)

## Holds

- **Default:** Lore holds no code specific to project-manage. Lore reaches
  project-manage through a `command` source and a `command` sink.
- **Default:** project-manage ships the adapters for Lore's contracts:
  `pm log` for the sink and `pm source` for the source.
- **Default:** each tool works without the other. A Lore feature that reads
  project-manage data also works with a wiki note or a repo file.
- **Default:** a roadmap item names a milestone by a ref that `lore_read`
  resolves. Lore reads the text behind the ref and does not parse its
  structure.
- **Incidental:** the project-manage adapter speaks the `command` source
  contract: JSON on stdin, hits with `pm:<id>` refs on stdout.

## Revisit if

- A project-manage feature needs structured fields that the source contract
  cannot carry. Then extend the generic contract instead of adding a
  project-manage kind.
- A team uses Lore with a different planning tool, and the contract does not
  fit that tool.

## Context

- Lore is a context machine for any team (PRD 0014, retire flags and trim Lore
  to a context machine). project-manage is a generic, self-hosted planning
  tool with plugins per organization (project-manage ADR 0011).
- Some people use both tools. A solo developer has no project-manage.
- ADR 0017 (the logbook) rejected a built-in project-manage sink. The
  `command` sink runs `pm log` with no project-manage code in Lore.
- A roadmap (`CONTEXT.md`) lives in the repo as `ROADMAP.md`. Its items can
  name project-manage milestones, which hold the dates. Lore sessions read
  both to guide decisions during design and build.

## Decision

- Lore and project-manage meet through Lore's generic contracts: sources for
  reading and sinks for writing.
- The project-manage side holds the adapters.
- A Lore feature reads project-manage data by a ref, through a source. A wiki
  note or a repo file can take the place of project-manage.

## Consequences / Trade-offs

- Easier: each tool ships, versions and breaks on its own. A team adopts one
  tool without the other.
- Easier: another planning tool plugs in with its own adapter.
- Harder: Lore cannot use project-manage structure such as `depends_on`
  directly. The adapter renders the structure as text. A change of shape is a
  change in project-manage.
- Harder: a defect at the seam can sit in either repo. The adapter's tests
  live in project-manage.

## Alternatives considered

- **A project-manage source kind in Lore.** Rejected: Lore would follow the
  project-manage API, and a solo developer would carry unused code.
- **The roadmap as a view over project-manage milestones.** Rejected: not
  every roadmap item deserves a milestone, a board per repo costs too much,
  and a sync between the repo and project-manage needs new infrastructure.
- **A roadmap file in Lore that copies the milestones.** Rejected: two homes
  for one fact (ADR 0014).
- **Lore reads the project-manage MCP server.** Rejected as Lore's path: the
  MCP server serves the agent's session, and an agent can use it next to Lore.
  Lore's own search needs a source.

## Status

Accepted.
