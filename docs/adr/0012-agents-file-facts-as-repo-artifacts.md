# 0012 — Agents file facts as repo artifacts; flags are retired

- **Status:** Accepted
- **Date:** 2026-09-16
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0014](../prd/0014-retire-flags-attach-facts-to-artifacts.md), epic [#419](https://github.com/buchbend/lore/issues/419);
  supersedes ADR [0007](0007-session-notes-retired-flags-are-the-crossing.md),
  ADR [0008](0008-flag-lands-marked-unreviewed.md) and
  ADR [0011](0011-flag-review-runs-in-a-local-ephemeral-browser-page.md)

## Context

ADR 0007 made the flag the only path from a session to the team wiki.
ADR 0008 let a flag land at write time, marked unreviewed, with a human
review walk to accept or decline it. ADR 0011 gave that walk a browser
page.

`lore status` on 2026-09-16 (host saiyajin, vault `~/git/vault`) shows
the outcome. Wiki ccat: 74 flags written, 79 pending, 0 accepted, 0
declined. The review walk never ran. The system-integration topic note
holds 51 flag blocks in 458 lines. The sampled flags are docs-versus-code
gaps with verified refs: bug reports. A bug report is transient. It does
not belong in a knowledge base, and a busy developer does not walk a
review queue.

Every agent-written wiki surface Lore shipped has died: session notes
(PRD 0013), briefings (PRD 0012), flags. Every artifact with a human
loop survived: issues, PRs, ADRs, PRDs, docs. GitHub issue state is
maintained by the developer during normal work and costs no extra step.

## Decision

Agents write nothing into the wiki on their own. An agent files each
fact as the repo artifact that already has a reader and a lifecycle.
The filing rule:

- A docs-versus-code gap, a trap or a missing fact becomes an issue. A trivial fix close to the session's work goes into the session's branch, named in the PR body.
- A fact about an existing issue or PR becomes a comment on it.
- A dead end becomes an issue closed as not-planned, with the reason in the close comment.
- A decision becomes an ADR opened as a PR. Exception: an ADR or PRD negotiated with the user in a grilling or domain-modeling session, which those skills write directly.
- A fact for a wiki topic note becomes a PR on the wiki repo with the edit.
- A cross-repo fact goes to the org knowledge repo: the wiki's own git remote.

Every agent-opened issue carries the label `agent-filed`. Every agent
comment opens with a line naming itself as agent-filed. An agent closes
only issues it opened. Agents post eagerly during the session and list
what they filed in their final message. Nothing blocks, nothing asks.

The flag write path, the review walk, the pending count and the flag
metrics are removed. The existing flag blocks are removed from the wiki
notes, not migrated. Git history keeps them.

## Consequences / Trade-offs

Easier: no review queue, no unreviewed marker, no second pending state.
Issue state answers "is this still true": open, fixed, or not planned.
The developer triages in the tracker, the one queue they already read.
The file-issue skill and `gh` are the write path; Lore ships no write
verb.

Harder: an agent-filed issue is public inside the org from the moment
it lands. The publish gate keeps scanning outbound text. Tracker noise
is the new failure mode; the label makes it filterable and measurable.
Facts that belong to no repo go to the knowledge repo, which needs a
tracker. The wiki grows only through human edits and agent PRs.

## Alternatives considered

- Keep flags, re-verify refs at read time, drop the review requirement. Rejected: the notes had already become unreadable dumps, and a flag block carries no state a reader can trust.
- Migrate the 74 flags into issues one-to-one. Rejected: 74 stale bug reports in one day; a fresh start costs less.
- Route facts to the wiki as PRs only. Rejected: a PR needs a human to merge; a bug report needs the tracker's state machine, not a merge.

## Status

Accepted. Supersedes ADR 0007, 0008 and 0011.
