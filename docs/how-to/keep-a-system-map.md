# Keep a system map

**Goal:** write down the edges between the repos of one system, so that an
agent in one repo knows its neighbours.

A **system map** is a wiki note at `<wiki>/systems/<name>.md` (PRD 0019, the
system map). The note lists the repos of one system and the edges between
them. Each edge names a consumer, a producer and the interface between them.
A **neighbour** is a repo one edge away from the current repo. Humans merge
every change to a map. The `/lore:map` skill proposes the changes.

## The shape of the note

```markdown
---
type: system-map
repos: [ccatobs/ops-db, ccatobs/data-center]
edges:
  - consumer: ccatobs/data-center
    producer: ccatobs/ops-db
    interface: "SQLAlchemy models"
    pin: sha            # sha | tag | image | package | none
    breaks_when: "a migration changes a table data-center reads"
---

# CCAT data system

Prose for humans: what the system does and how data flows.
```

- Name each repo `owner/repo`, as its git remote names it. Folder names differ between people.
- `consumer`, `producer` and `interface` are required. `pin` and `breaks_when` are optional.
- Each repo of an edge is in `repos`.
- Agents read the frontmatter. The body is for humans.
- A repo can appear in more than one map. A wiki can hold several maps.
- Edges inside one repo do not go here. `CONTEXT-MAP.md` holds them.

## See the neighbours

SessionStart adds a neighbours block for the current repo, before the glossary
index. The block holds one line per edge and 600 characters at most.

```
## Neighbours (system map)
- uses ccatobs/ops-db: SQLAlchemy models; pin: sha; breaks when: a migration changes a table data-center reads
- used by ccatobs/web: REST API
```

- A repo in no edge gets no block.
- Past the budget, the block lists the edges that fit and ends with `… N more: lore map <owner/repo>`.

## Print and check the map

```
lore map                          # every map of every wiki
lore map ccatobs/ops-db           # the edges of one repo, from every map
lore map ccatobs/ops-db --json    # the same edges as a JSON list
lore map check                    # validate the format of every map
lore map --wiki ccat              # read one wiki only
```

- `lore map check` exits 1 on an edge without `consumer`, `producer` or `interface`, on an unknown `pin`, and on a repo that `repos` lacks. Each error names the note and the edge.
- The check validates the format only. The check does not test that an edge is true.

## See the map in the browser

```
lore stats --page systems
```

`lore stats` writes two static pages into `<lore_root>/.lore/` and links them:
`stats.html` (token use) and `systems.html` (the system map). The systems page
holds:

- a diagram per system: each producer above its consumers, with an arrow to each consumer. A long edge runs in a lane between the boxes. The line style shows the pin, and a legend names each style. Hover a repo to highlight its edges and neighbours, and click it for its card;
- the map's prose and a table of its edges;
- the system's target branch, how many checkouts are on it, and an orange border on each box that is off it;
- one card per repo: its systems, scope, checkout folder, current branch, local work, and one line on what the repo is. That line is the `pyproject.toml` or `package.json` description, else the README's first paragraph;
- one row per scope: the checkouts below its folder, with the ones in no map marked;
- "What is a system map?", the terms of the map.

A dashed box has no checkout below a scope folder. The page has no script and
makes no network request.

## Find the checkouts

The map names repos as `owner/repo`, never folders: folders differ between
people. Lore finds each checkout on your machine:

1. A `checkouts` entry in `~/.config/lore/config.yml` names its folder.
2. Otherwise Lore looks below each scope folder, three levels deep, and names each checkout by its git remote.

```yaml
scopes:
  ccat:
    path: ~/src/ccat
checkouts:                         # only for repos the search misses
  ccatobs/archive: /data/archive-clone
```

- An entry wins over the search. Lore checks that the folder's remote is that repo. On a missing folder or another remote, Lore warns and searches instead.
- Use an entry for a repo outside every scope folder, or to choose one of several clones.
- Each person keeps their own entries. Nothing about folders goes into the wiki.

## See the state of a system

```
lore system status                  # every system
lore system status ccat             # one system
lore system status ccat --fetch     # fetch each checkout first
lore system status ccat --json      # the same as JSON
```

`lore system status` is `git status` for every repo of a system, producers first:

```
ccat · target feat/x (saved, set 2026-10-25) · 1 of 3 checkouts on target
  ✗  ccatobs/ops-db       main  clean              → switch                   ~/src/ccat/ops-db
  ✗  ccatobs/data-center  main  1 changed files    → settle local work first  ~/src/ccat/data-center
  ✓  ccatobs/web          main  1 untracked files                             ~/src/ccat/web
  -  ccatobs/archive      no checkout: add `checkouts: {ccatobs/archive: <folder>}` to ~/.config/lore/config.yml
  pin: ccatobs/data-center pins ccatobs/ops-db by sha
```

- Each line shows the branch, the local work, ahead and behind the upstream, the step to the target, and the folder. `(pinned)` marks a folder from `checkouts`.
- Local work is a merge, rebase, cherry-pick, revert or bisect in progress, unmerged files, changed files, untracked files, or commits on no remote.
- The command changes nothing. Only `--fetch` reaches the network.
- The same repos in the same git state give the same output.

## Set the target

The target is the branch a system belongs on, such as a feature branch that
spans repos. Lore keeps it on your machine, in `<lore_root>/.lore/system-target/`.

```
lore system target feat/x ccat      # remember feat/x for ccat
lore system target ccat             # print it   (with one map: `lore system target`)
lore system target --clear ccat     # forget it
lore system status ccat --branch main   # compare with main once; saves nothing
```

A repo without the target branch belongs on its default branch.

## Bring a system to its target

Run `/lore:system [branch] [system]`. Without a branch the skill uses the saved
target. The skill:

1. Shows `lore system status`, after a fetch.
2. Offers to save a new branch as the target.
3. Walks the repos producers first and offers to settle each repo's local work. It offers a commit, a push, a `.gitignore` line, or to finish or abort a merge or rebase. You can also leave the repo as it is. Before a commit, it shows the diff and proposes a message.
4. Switches each checkout and pulls with `--ff-only`. A pull that fails shows both sides and asks.
5. Names the pinned edges that lag the target, and offers a `checkouts` line for each repo without a checkout.
6. Shows `lore system status` again.

The skill acts only after you say yes, one repo at a time. It never runs `reset`,
`clean`, `checkout -- <file>`, a branch delete or a force push; to throw work away,
it shows the command and you run it. It never moves a pin.

| action | what the skill does |
|---|---|
| `switch` | `git switch <target>`, then `git pull --ff-only` |
| `pull` | on the target already: `git pull --ff-only` |
| `default` | the repo has no branch of that name: its default branch, then pull |
| `stop` | the repo holds local work you chose to keep; it stays as it is |
| `blocked` | another worktree holds the branch; it stays as it is |

## Propose map changes

Run `/lore:map` inside a scope folder. The skill runs `lore map scan --json`,
reads the code around each candidate edge and opens one wiki PR.

```
lore map scan --json                  # the cwd's scope folder
lore map scan --scope ~/src/ccat      # another folder
```

- The scan reads git submodules, git URLs and package names in `pyproject.toml` and `package.json`, `FROM` images in Dockerfiles and images in compose files.
- The scan reads a Python package name from `pyproject.toml`, `setup.cfg` or `setup.py`. An image tag such as `${TAG:-main}` counts as a tag.
- The scan names each checkout by its remote. Where a main checkout and a worktree of one repo both sit in the folder, the scan reads the main checkout.
- A dependency that no checkout provides stays out. A package name that two checkouts declare is listed under `ambiguous`, with no candidate edge.
- The scan calls no model and no network.
- The wiki PR has three sections: added, changed, and edges the scan no longer finds. The skill deletes no edge. A human deletes one in the PR.
- SessionStart runs the scan in a background child, once an hour per scope. The banner names the count of candidate edges that the map lacks.

## Tidy the agent files

After the map PR merged, run `/lore:map` again. The tidy phase edits each
repo's `AGENTS.md` or `CLAUDE.md`, in one PR per repo.

- The skill removes text about another repo where the map holds the same fact.
- The skill adds one line that links the map.
- The skill keeps the repo's commands, conventions and release steps. Text that may be a repo rule stays.

Repos that colleagues without Lore read can keep their text. Switch the tidy
phase off for the scope in your config:

```yaml
scopes:
  ccat:
    path: ~/src/ccat
    map:
      tidy: false
```

## Plan and build across repos

- `orient`, full mode, starts its cross-repo explorer from `lore map <owner/repo> --json`.
- `to-epic` names the edge's `pin` in a build-order row that crosses repos.
- `build`, `epic` mode, reads the `pin` before it dispatches a consumer.
- A repo in no map keeps the old behaviour in each skill.
