---
title: Logbook entries at every finish point, and search sources
status: draft
epic: https://github.com/buchbend/lore/issues/467
repos:
  - buchbend/lore
---

# PRD 0017: Logbook entries at every finish point, and search sources

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/467).
> The epic links here; this file is not embedded in the issue body.
> Decisions: ADR [0017](../adr/0017-the-logbook.md), ADR
> [0013](../adr/0013-search-over-wiki-and-github-is-federated-not-cached.md)
> (amended by this epic). Builds on PRD [0016](0016-routing-settings-and-setup.md).

## Problem

- Lore retired session notes, digests and briefs. No artifact now says who
  worked on what, day by day. A team lead asks that question every morning.
- project-manage collects logbook entries into daily reports. Nobody types
  an entry after an AI-assisted build, so the report misses that work.
- `lore_search` reads the wiki index and GitHub only. A team wiki on another
  system, such as a DokuWiki that needs a token, stays invisible to agents.
- GitHub search is wired into the search handler directly. A second source
  has nowhere to plug in.

## Solution

- At every finish point, and at a stop that hands work over, the agent
  writes one logbook entry. Lore delivers it to every sink of the scope.
- The default sink is a monthly file next to the PRDs and ADRs. A team adds
  a `command` sink, such as the `pm` client or a Matrix webhook.
- An entry stays in the local ledger until every sink confirms delivery. The
  SessionStart and session-end hooks retry it.
- `lore_search` returns the wiki list and one list per source. A person adds
  a source to a scope by name, such as the team DokuWiki.

## Implementation decisions

### Logbook entry

- `lore log add --ref <ref> --text-file <path>` takes the agent's paragraph.
  The ref is a PR, an issue, an epic or `branch@sha`.
- The CLI rejects an entry that breaks a content rule, with exit code 2 and
  the rule's name:
  - more than one paragraph, or more than 80 words;
  - no Markdown link;
  - a line that starts with `todo:` or `decided:`.
- The CLI adds the author, the date, the repo and the scope from the route.
- The entry key is `lore:<owner/repo>:<ref>`. Every sink uses the key to
  skip a repeated entry.

### Sinks

- A scope's `logbook` key lists sink names. The default list is `[file]`.
  The `sinks` section of the person's config defines each name.

```yaml
sinks:
  pm-ccat: {kind: command, run: "pm log CCAT-DEV"}
  matrix:  {kind: command, run: "curl -fsS -d @- $HOOKSHOT_URL", secret: hookshot}
```

- **file:** Lore writes `docs/logbook/YYYY-MM.md` in the repo, newest entry
  first, under a month heading. A hidden marker with the key makes a repeat a
  no-op. Lore adds `docs/logbook/*.md merge=union` to `.gitattributes` once.
- **command:** Lore runs the command with the entry text on stdin. It sets
  `LORE_ENTRY_KEY`, `LORE_ENTRY_AT`, `LORE_ENTRY_REPO`, `LORE_ENTRY_SCOPE` and
  `LORE_ENTRY_REF`. A named secret arrives as `LORE_SECRET`. Exit code 0
  means delivered.
- Lore runs the sinks in parallel, each with a timeout of 30 seconds.
- `lore setup sink file|command` adds a sink and tests it without posting.

### Pending entries

- `lore log add` writes a `[logbook]` line into the local ledger first. The
  line holds the entry and the sinks that confirmed delivery.
- The local ledger is the file in the repo's git directory, in every build
  mode. The epic board keeps no logbook line.
- `ledger-check` does not block on a `[logbook]` line.
- `ledger-archive` carries undelivered `[logbook]` lines into the new ledger.
- `lore log flush` retries every undelivered line in the current repo. The
  SessionStart hook and the session-end hook run it in the background.
- The SessionStart banner and `lore status` show the count of undelivered
  entries.

### Build rule

- The `build` skill gets one rule for every mode. At the finish point, the
  agent writes the entry and runs `lore log add`. The step comes after the
  handover section and before the merge approval.
- The agent commits the `file` sink's change on the build branch, before the
  version bump.
- The `handover` skill runs the same step at a stop that hands work over.
  The entry may add a `Waiting on:` line.
- The entry names no merge state.

### Search sources

- A source has a name, a kind and an optional secret name. It answers
  `search(query, k)` with hits: ref, title, URL and snippet. A source kind can
  also answer `read(ref)`.
- Kinds: `github` (today's `gh search issues`), `dokuwiki` (JSON-RPC
  `core.searchPages` and `core.getPage` with a bearer token) and `command`
  (JSON on stdin, JSON hits on stdout).
- The tracker's source and the scope's `sources` names together form the
  source list. Without a route, every defined source runs.
- `lore_search` and `lore search` return the following shape:

```json
{"wiki": [...],
 "sources": [{"name": "github", "kind": "github", "hits": [...], "note": null},
             {"name": "ccatwiki", "kind": "dokuwiki", "hits": [], "note": "timeout after 5s"}]}
```

- Each source runs in its own thread with its own timeout, 5 seconds by
  default and 10 seconds for GitHub. A failed source gives no hits and a note.
- A hit ref is `<source>:<id>`. `lore_read` sends such a ref to its source.
  GitHub refs keep the `owner/repo#n` form and the live read through `gh`.
- `lore setup source dokuwiki|command` adds a source and runs one test query.
- This epic amends ADR 0013: a wiki list plus one list per source. Lore
  merges no lists and caches nothing.

## Testing decisions

- Test the content rules on `lore log add`, one case per rule.
- Test that the `file` sink writes newest first, and that a repeated key
  writes nothing.
- Test the `command` sink with a stub script: text on stdin and the
  environment variables.
- Test fan-out: one failing sink leaves its ledger line undelivered for that
  sink only. `lore log flush` then delivers it.
- Test that `ledger-archive` carries an undelivered line forward, and that
  `ledger-check` passes with one.
- Test the search shape with a fake source that times out. The other lists
  still return.
- Test the `dokuwiki` kind against a recorded JSON-RPC response. Use no live
  wiki in the suite.
- Test that a secret value reaches a source or a sink only through its name.
- Prior art: `tests/test_workflow_ledger_cmd.py`, the `lore_search` tests of
  the MCP server, and `lib/lore_core/gh.py` as the transport pattern.

## Out of scope

- An `http` sink kind. ADR 0017 adds one when a command proves too clumsy.
- A day roll-up. The project-manage report kind groups entries by date.
- Caching any source (ADR 0013).
- The `--source` flag on `pm log`. project-manage tracks that change.
- The folder tracker and its search source: the folder tracker seed issue.
