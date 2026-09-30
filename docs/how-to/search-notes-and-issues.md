# Search notes and issues

**Goal:** find a fact in the wiki notes and in the GitHub issues and pull
requests, with one call.

`lore_search` is a federated search. It runs two lookups and returns two
lists. It never merges them into one ranking.

- `wiki` holds ranked note paths from the local index.
- `artifacts` holds issues and PRs from one live `gh search issues` call. Each entry has an `owner/repo#number` ref, a title, a state, a URL and an update time.

## Before you start

- `gh` is installed and signed in, so the `artifacts` list can fill.
- The repo is attached (`lore attach`), or the wiki has a git remote. The GitHub search targets the attached repo and the wiki's own remote.

## Search

Ask the agent to call the `lore_search` MCP tool. The `lore search <query>`
shell command searches the wiki index only and returns no GitHub hits.
Pass `for_repo` (`org/name`) to boost notes tagged with that repo and to
point the GitHub search at it. Pass `wiki` to scope the note search to one
wiki.

## When GitHub cannot answer

If no repo is attached, `gh` fails, or the machine is offline, the result
holds the `wiki` list and a `note` field. The note names the omission. An
empty `artifacts` list with no note means GitHub answered and found nothing.

## Read the live artifact before you act

Lore stores no artifact text from search. Search is for finding. Before you
comment on an issue, close it, merge a PR or poll its state, read it live:

```bash
gh issue view <number> --repo <owner>/<repo>
```

For the issues the session works on, the context pack (`lore_context_pack`)
already carries the number, title, state and body. The common case needs no
search.
