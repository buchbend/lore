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

- a diagram per system: each producer above its consumers, an arrow to each consumer, the pin on the arrow;
- the map's prose and a table of its edges;
- one card per repo: its systems, scope, checkout folder, current branch, changed files, and one line on what the repo is (the `pyproject.toml` or `package.json` description, else the README's first paragraph);
- one row per scope: the checkouts below its folder, with the ones in no map marked;
- "What is a system map?", the terms of the map.

A dashed box has no checkout below a scope folder. The page has no script and
makes no network request.

## Switch a system to one branch

Run `/lore:checkout <branch> [system]`. The skill puts each checkout of the
system's repos on the branch, producers first.

```
lore map status --system ccat --json                   # every checkout of the map, producers first
lore map status --branch feat/x --system ccat --json   # the same, with the action for the branch
```

Each checkout gets one action:

| action | what the skill does |
|---|---|
| `switch` | `git switch <branch>`, then `git pull --ff-only` |
| `pull` | already on the branch: `git pull --ff-only` |
| `default` | no branch of that name: switch to the default branch and pull |
| `skip` | the checkout holds changed files; it stays as it is |
| `blocked` | another worktree holds the branch; it stays as it is |

- The skill fetches first. `lore map status` reads the local refs only.
- The skill never stashes, resets, rebases, forces or pushes.
- `pinned` lists the edges whose consumer pins the producer by `sha`, `tag`, `image` or `package`. A producer on the new branch does not reach such a consumer until the pin moves. The skill names each one and leaves the pin alone.

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
