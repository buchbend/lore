# Search notes, issues and other sources

**Goal:** find a fact in the wiki notes, in GitHub issues and pull
requests, and in any other source your scope names.

`lore search` and the `lore_search` MCP tool run one lookup per source. They
return one list per source. They never merge the lists into one ranking, and
they cache nothing.

## Read the result

`lore search <query> --json` prints schema `lore.search/2`:

```json
{
  "wiki": [{"path": "...", "wiki": "...", "score": 0.9}],
  "sources": [
    {"name": "github", "kind": "github", "hits": [], "note": ""},
    {"name": "teamwiki", "kind": "dokuwiki", "hits": [], "note": ""}
  ]
}
```

- `wiki` holds ranked notes from the local index, each with a path, a score and a description.
- `sources` holds one entry per source: its `name`, its `kind`, its `hits` and a `note`.
- A GitHub hit has an `owner/repo#number` ref, a title, a state, a URL and an update time.
- A `dokuwiki` or `command` hit has a `<source>:<id>` ref, a title, a URL and a snippet.

The `artifacts` key of the earlier schema is gone. The GitHub hits now sit in
the `github` source.

## Before you start

- A scope routes the repo to the wiki (`lore setup scope`), or the wiki has a git remote.
- For GitHub hits, `gh` is installed and signed in.

## Choose the sources

`github` is always defined. A scope or a `.lore.yml` that lists `sources`
runs exactly those sources. Add `github` to the list to keep it.
With no `sources` list, every defined source runs.

```yaml
sources: [github, teamwiki]
```

## Add a source

DokuWiki, with a token. Lore prompts for the token and stores it in
`~/.config/lore/secrets.yml`:

```
lore setup source dokuwiki --name teamwiki --url https://wiki.example.org
```

A command that speaks JSON:

```
lore setup source command --name notes --run 'my-search-tool'
```

Each command writes the definition to `~/.config/lore/config.yml` and runs one
test query. It prints the hit count, or the reason the query failed. See
[config](../architecture/config.md) for the definition keys.

### The command contract

Lore writes `{"op": "search", "query": "...", "k": 5}` to the command's
standard input. The command prints JSON hits on standard output.

## When a source cannot answer

Each source has its own timeout: 5 seconds, 10 seconds for `github`, or the
`timeout` key of the source. A source that fails or times out returns
`hits: []` and a `note` that names the reason. The other sources still answer.
An empty `hits` list with an empty `note` means the source answered and found
nothing.

## Read a hit

Call `lore_read` with the ref from a `dokuwiki` or `command` hit:

```
teamwiki:projects:plan
```

Lore sends the ref to that source and returns the page. A `github` ref keeps
the live `gh` read:

```bash
gh issue view <number> --repo <owner>/<repo>
```

Read the live artifact before you comment on an issue, close it, merge a PR
or poll its state. Search is for finding.

For the issues the session works on, the context pack (`lore_context_pack`)
already carries the number, title, state and body. The common case needs no
search.
