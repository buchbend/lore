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

## Propose map changes

Run `/lore:map` inside a scope folder. The skill runs `lore map scan --json`,
reads the code around each candidate edge and opens one wiki PR.

```
lore map scan --json                  # the cwd's scope folder
lore map scan --scope ~/src/ccat      # another folder
```

- The scan reads git submodules, git URLs and package names in `pyproject.toml` and `package.json`, `FROM` images in Dockerfiles and images in compose files.
- The scan names each checkout by its remote. A dependency that no checkout provides stays out.
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
