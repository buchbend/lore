# ADR 0020: SessionStart delivers the index; AGENTS.md holds static pointers only

- **Status:** Accepted
- **Date:** 2026-11-10
- **Deciders:** Christof Buchbender
- **Relates to:** issue [#574](https://github.com/buchbend/lore/issues/574)
  (retire the generated index block of AGENTS.md); issue
  [#567](https://github.com/buchbend/lore/issues/567) (keep the AGENTS.md
  index block current in every harness), closed as not planned; PRD
  [0020](../prd/0020-index-first-context-and-lab-notebook.md) (index-first
  context and the lab notebook), section "Index"
- **See also:** ADR [0014](0014-one-home-per-fact.md) (one home per fact),
  ADR [0013](0013-search-over-wiki-and-github-is-federated-not-cached.md)
  (search over wiki and GitHub is federated, not cached)

## Holds

- **Invariant** (test: `tests/test_repo_index.py::test_session_start_injects_the_full_index_beside_an_old_agents_block`):
  SessionStart injects the full index and reads neither `AGENTS.md` nor
  `CLAUDE.md` for the index.
- **Default:** the index has two delivery paths: SessionStart injects the
  index, and `lore index` prints the index. Both read the inputs at call
  time. SessionStart leaves the neighbours group out, because the banner
  holds the neighbours above the index.
- **Default:** no file holds a generated copy of the index. Lore ships no
  command that writes or checks such a copy.
- **Default:** `AGENTS.md` holds static pointers only. The scaffold appends
  one paragraph, `## Where to look`, that names the home of each kind of
  fact.
- **Incidental:** the scaffold names only the homes that exist at the time
  of the run, and leaves an existing `## Where to look` paragraph as it is.
- **Incidental:** Lore ships no migration code. A person removes an old
  block by hand.

## Revisit if

- A team works mostly in a harness that runs no SessionStart hook and no
  `lore` command.
- A reader without Lore reports that the pointer paragraph is not enough to
  find a decision or a term.

## Background

PRD 0020 (index-first context and the lab notebook) added a generated copy
of the index to `AGENTS.md`, between two markers. The copy served a reader
without Lore. No step regenerated the copy, so the copy went stale after
each new ADR, PRD, term or roadmap heading. Issue #567 asked for a git
pre-commit hook to keep the copy current.

ADR 0014 (one home per fact) is the reason for the decision. Each ADR title,
PRD title, term and roadmap heading already has a home. The copy was a
second home for all four, and a second home needs a job that keeps it true.
A committed copy also cannot hold a fact that differs per machine, such as
the neighbours of the system map.

PRD 0020 is shipped and stays as written. This ADR replaces the part of its
section "Index" that describes the block in `AGENTS.md`.

## Alternatives considered

- **A git pre-commit hook that regenerates the block** (issue #567).
  Rejected: each clone needs the hook installed, and a commit from a clone
  without the hook leaves the copy stale again.
- **A CI job that fails on a stale block.** Rejected: the job turns each new
  ADR or term into a second required edit, for a copy that sessions with
  Lore do not read.
- **Keep the block and the stale hint in the banner.** Rejected: the
  copy stays a second home, and the hint asks a person for an edit after
  each new ADR or term.
- **No text for a reader without Lore.** Rejected: the pointer paragraph
  costs three lines and does not go stale when a document is added.

## Amendments

None.
