# Route repos to a wiki

**Goal:** make every repo under a folder use one wiki, give one repo an
exception, and store a token Lore needs.

A **scope** is a folder in `~/.config/lore/config.yml`. Every repo below it
routes to the scope's wiki. A new repo there needs no step (ADR 0016).

## Add a scope

From a repo no scope covers:

```
lore setup scope
```

Setup offers the folder above the repo. Without a terminal, pass the answers:

```
lore setup scope --yes --name ccat --wiki ccat --remote 'github.com/ccatobs/*'
```

The result is one entry in the person config:

```yaml
scopes:
  ccat:
    path: ~/git/ccatobs
    wiki: ccat
    remote: ['github.com/ccatobs/*']
defaults:
  tracker: auto
```

`defaults` holds the values a repo gets when no scope sets them. Keys are
documented in [config](../architecture/config.md).

## Give one repo an exception

Commit a `.lore.yml` at the repo root, or in a subfolder for that subtree. It
holds exceptions only: the tracker, the sinks (`logbook`) and the sources, by
name. For example, opt this repo out of a team sink:

```yaml
logbook: [file]
```

These keys in `.lore.yml` win over the scope. A `.lore.yml` does not choose
the wiki or the scope (ADR 0016). Lore ignores a `wiki:` or `scope:` key there,
and `lore status` names the key. The wiki comes from your scopes
(`lore setup scope`). Environment variables (`LORE_WIKI`, `LORE_SCOPE`,
`LORE_TRACKER`) win over both.

## See where a value came from

```
lore status
```

prints each route value for the current folder with its origin: environment,
`.lore.yml`, scope, `defaults` or code default.

## Store a secret

Config files hold no tokens. A `token` or `password` key in
`~/.config/lore/config.yml` fails validation. Name the secret in config and
store the value:

```
lore config secret set PM_TOKEN
lore config secret list
lore config secret unset PM_TOKEN
```

The values live in `~/.config/lore/secrets.yml` (mode 0600). `list` prints
names only. Lore refuses a secrets file inside a git worktree.
