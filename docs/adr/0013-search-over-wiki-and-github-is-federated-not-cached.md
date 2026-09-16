# 0013 — Search over wiki and GitHub is federated, not cached

- **Status:** Accepted
- **Date:** 2026-09-16
- **Deciders:** Christof Buchbender
- **Relates to:** PRD [0014](../prd/0014-retire-flags-attach-facts-to-artifacts.md);
  ADR [0012](0012-agents-file-facts-as-repo-artifacts.md)

## Context

The search query log (`~/.cache/lore/query-log.jsonl`, April to
September 2026) holds about 2000 agent lookups through `lore_search`.
The transcripts for the same period hold about 500 `gh` read calls and
no `gh search` call. Agents choose the local ranked search four to one.
Over 90 percent of hits land on wiki topic notes.

ADR 0012 stops every agent write to the wiki. Topic notes become
human-only. The living record of the work moves to issues and PRs. The
retrieval path agents use loses its source of fresh content unless
issues and PRs become searchable through the same call.

Two reviews on 2026-09-16 tested a local markdown copy of issue and PR
text. Reading a known artifact from disk saves no tokens: issue #417
measures 3360 bytes via `gh issue view --json` and 3151 bytes as cached
markdown. The one-call incremental sync (`gh ... list --search
"updated:>="`) silently caps every comment thread at 100 entries,
cannot see deletions or transfers, and runs on the search API's
30-per-minute limit. The heavy `gh` users are orchestration sessions
polling state that changed minutes earlier, which a copy would serve
wrong.

## Decision

`lore_search` runs the wiki index query and one live `gh search issues`
call against the attached repo and the wiki's git remote. The result
holds two lists: wiki hits, then artifact hits. GitHub ranks its own
list; Lore does not merge rankings. Nothing is stored. When `gh` fails
or the machine is offline, the tool returns the wiki list and names the
omission.

Search is for finding. Before an agent comments, closes, merges or
polls, it reads live through `gh`.

The context pack adds the body to the fields it already fetches for the
session's focus issues. The common case then needs no search at all.

## Consequences / Trade-offs

Easier: one call, one habit, issues beside notes, always fresh. No sync
job, no cache directory, no private-repo text at rest, no staleness
rule. About fifty lines in the MCP server.

Harder: every search costs one network call, one to two seconds. Offline
work loses the artifact list. The search API allows 30 calls a minute;
the log shows about 20 lookups a day, so the limit is far away. A future
non-GitHub tracker needs its own live call.

## Alternatives considered

- Local markdown copy under `$LORE_CACHE`, fed into the FTS index. Rejected for now: no token saving on reads, a broken sync spec, and a freshness problem Lore would have to own. Kept as the upgrade path if retrieval-miss feedback reports offline work or rate limits. The spec for that day has six parts. One full list per repo diffed on update time. Re-fetch with `gh view` at exactly 100 comments. Sync time in frontmatter. A distinct index type with a capped share of results. Secret scanners on ingest. Directory mode 0700.
- A skill line telling agents to run `gh search issues` themselves. Rejected: two tools with two rankings, and every skill and every agent has to remember both.
- `gh api --cache`. Rejected: HTTP caching for repeat reads only; no search, no ranking.

## Status

Accepted.
