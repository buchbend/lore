# Route repos to a wiki

**Goal:** make every repo under a folder use one wiki, give one repo an
exception, and store a token Lore needs.

A **scope** is a folder in `~/.config/lore/config.yml`. Every repo below it
routes to the scope's wiki. A new repo there needs no step (ADR 0016).

## Add a scope

Name the folder:

```
lore setup scope ~/git/ccatobs
```

Without a path, setup offers the folder you are in. Inside a repo, it offers
the folder above the repo. Without a terminal, pass the answers:

```
lore setup scope ~/git/ccatobs --yes --name ccat --wiki ccat --remote 'github.com/ccatobs/*'
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

## Route by git remote, wherever you clone

A scope can match a repo by its git remote alone. A remote-only scope does
not depend on your folder layout:

```
lore setup scope --remote-only --wiki ccat
```

In a repo with the remote `github.com/ccatobs/data-center`, setup suggests
`github.com/ccatobs/*` and names the scope `ccatobs`. Pass `--remote` and
`--name` to choose other values. GitLab remotes work the same way, and `*`
matches nested groups too: `gitlab.example.org/group/*` matches
`gitlab.example.org/group/sub/repo`.

A folder match wins over a remote match (ADR 0016). A broad folder scope,
such as `~/git`, therefore hides every remote scope for repos below it.
`lore config` names a remote scope that lost to a folder match.

## Let the wiki declare its repos

A team wiki can name the remotes it serves in its `.lore-wiki.yml`:

```yaml
routing:
  scope: ccat
  remote: ['github.com/ccatobs/*']
```

Every person who mounts the wiki then routes those repos to it, with no
scope of their own. A change reaches them with the next wiki pull. Your own
scopes win over a wiki's patterns. A wiki never names a sink. When your
config has a scope with the same name, such as `ccat` with
`logbook: [file, pm]`, its sinks apply.

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
lore config
```

prints the whole wiring: each wiki with its git remote, each scope with its
folder and remote patterns, the defaults, the sinks and the sources. Then
it prints the route of the current folder, with the origin of each value:
environment, `.lore.yml`, scope, `defaults` or code default. A `!` marks a
line that needs a look, such as a missing scope folder or a wiki without
git. `lore config --cwd <folder>` explains another folder.

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
