# ADR 0017: The logbook

- **Status:** Accepted
- **Date:** 2026-10-11
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0017](../prd/0017-logbook-and-search-sources.md), epic [#467](https://github.com/buchbend/lore/issues/467),
  design round "Lore Full Circle" (2026-10-11).
- **See also:** ADR [0014](0014-one-home-per-fact.md) (homes; amended with the logbook row),
  ADR [0016](0016-routing-by-folder-scope.md) (the scope that selects the sinks)

## Holds

- **Default:** at each finish point, and at a stop that hands work over,
  the agent writes one logbook entry. No person types it.
- **Default:** an entry is one paragraph of about 60 words or fewer. It holds
  at least one link to the home of a fact: a PR, an issue, a PRD or an ADR.
  It restates no fact that has a home.
- **Default:** `lore log add` rejects an entry with a line that starts with
  `todo:` or `decided:`. project-manage turns such lines into tasks and
  decisions.
- **Default:** an entry carries no merge state. A commit on `main` shows the
  merge. A PR link shows an open branch. An optional `Waiting on:` line names
  a blocker.
- **Default:** Lore delivers each entry to every sink of the scope in
  parallel, right after the agent writes it. The merge decision does not
  delay delivery.
- **Default:** Lore keeps an entry as a `[logbook]` line in the local ledger
  until every sink confirms delivery. The SessionStart hook and the
  session-end hook retry the undelivered lines. A failed delivery does not
  block a merge.
- **Default:** the `file` sink writes `docs/logbook/YYYY-MM.md`, newest
  entry first. The agent commits the file on the build branch.
- **Incidental:** two sink kinds ship, `file` and `command`. A `command`
  sink gets the entry text on stdin and the idempotency key, date and repo
  in environment variables. Exit code 0 means delivered.
- **Incidental:** the `.gitattributes` line `docs/logbook/*.md merge=union`
  avoids most merge conflicts in the monthly file.

## Revisit if

- People dismiss many lore entries from their daily reports.
- Web merges of the monthly file conflict twice. Then switch to one file per
  entry.
- A target needs more than a command can give, such as structured fields.
  Then add an `http` kind.

## Context

Lore retired session notes, digests and briefs (ADR 0007, PRD 0013). Facts
now live in repo artifacts (ADR 0012). No artifact answers the question a
team lead asks each morning: who worked on what?

project-manage has logbooks and daily reports. A report kind lists
logbooks and shows their entries for a date. Its API accepts an entry with
a `source` key and returns the existing entry for a repeated key. The `pm`
command-line client reads entry text on stdin (`pm log <logbook>`). A Matrix
room can also feed a logbook.

A solo developer has no project-manage. Lore needs a default that works
with a local repo only.

## Decision

- The logbook is a new home: "Who worked on what, and when?" The `build`
  skill writes it at its finish point.
- An entry points to the homes and restates nothing.
- A scope lists its sinks. The default list is `[file]`. A team adds a
  `command` sink, such as `pm log CCAT-DEV` or a curl call to a Matrix
  webhook.
- Delivery runs at once, to all sinks in parallel. Undelivered entries wait
  in the local ledger, and the hooks retry them.

## Consequences / Trade-offs

- Easier: daily reports fill without typing. A colleague adopts the habit by
  adding one sink to a scope.
- Easier: the solo default needs no network and no account.
- Harder: a remote sink receives entries for work that does not merge. The PR
  link in the entry shows the state.
- Harder: an expired token holds entries back. `lore status` and the
  SessionStart banner show the count of undelivered lines.

## Alternatives considered

- **Post to remote sinks only after the merge.** Rejected: an entry for
  deferred or unmerged work gets lost, and the build gets one more wait.
- **A merge-state line, updated after the merge.** Rejected: a second write
  per entry for a fact the commit and the PR link already show.
- **A day roll-up written by Lore.** Rejected: the project-manage report kind
  already groups entries by date.
- **A separate host-level outbox.** Rejected: the ledger already holds the
  run's open items, and the hooks already read the ledger.
- **A built-in project-manage sink.** Rejected: the `command` kind covers
  the `pm` client and Matrix with no code specific to project-manage.
