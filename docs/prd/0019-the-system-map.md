---
title: The system map
status: draft
epic: https://github.com/buchbend/lore/issues/531
repos:
  - buchbend/lore
---

# PRD 0019: The system map

> Source of truth for this epic. Tracker: [epic issue](https://github.com/buchbend/lore/issues/531).
> The epic links here; this file is not embedded in the issue body.

## Problem

A repo rarely stands alone. In a scope such as `ccatobs`, `data-center` reads the tables that
`ops-db` defines, and other repos consume the REST API of `data-center`. Today no artifact
holds these edges in a form an agent can use.

- In repo A, nothing tells the agent that A consumes the schema of B, or that C pins a release
  of A.
- `orient` rediscovers the edges each time, with a cross-repo explorer that scans blind.
- `build` handles a cross-repo pin in `epic` mode without a list of consumers.
- Teams write cross-repo knowledge into each repo's `CLAUDE.md` or `AGENTS.md`. The same edge
  then sits in two files, and the two copies drift.

ADR 0014 (one home per fact) already names the home: what spans several repos lives in a wiki topic note, edited
through a PR. The note has no structure an agent can read, and no tool keeps it true.

## Solution

A **system map** is a wiki note that lists the repos of one system and the edges between
them. Each edge names a consumer, a producer and the interface between them.

- SessionStart shows the current repo's **neighbours**: one line per edge, about 600
  characters at most.
- `lore map` prints a map, or one repo's edges, as text or JSON.
- The `lore:map` skill walks the local checkouts of a scope and proposes changes to the map
  in one wiki PR. After that PR merges, the skill tidies each repo's `AGENTS.md` or
  `CLAUDE.md`: it removes text that the map now holds, in one PR per repo.
- `orient`, `to-epic` and `build` read the edges instead of scanning.

Humans merge every change to the map. Agents propose changes.

## Implementation decisions

### The map

- One note per system at `<wiki>/systems/<name>.md`. A wiki can hold several systems. A repo
  can appear in more than one map.
- Repos are named `owner/repo`, from the git remote, not by folder. Scopes are per person,
  and folder layouts differ between people.
- Edges run between repos only. Relationships inside one repo stay in `CONTEXT-MAP.md`.
- The frontmatter holds the edges. The body is prose for humans: what the system does and how
  data flows. Agents read the edges, not the prose.

```yaml
type: system-map
repos: [ccatobs/ops-db, ccatobs/data-center]
edges:
  - consumer: ccatobs/data-center
    producer: ccatobs/ops-db
    interface: "SQLAlchemy models"
    pin: sha            # sha | tag | image | package | none
    breaks_when: "a migration changes a table data-center reads"
```

- `consumer`, `producer` and `interface` are required. `pin` and `breaks_when` are optional.
- A repo's project orientation note links the map. It does not repeat the edges.

### Reading the map

- A module in `lore_core` parses the maps of a wiki and returns the edges of a repo. The
  module uses the standard library and the existing frontmatter parser only.
- SessionStart adds a neighbours block before the glossary index. A repo without an edge gets
  no block.
- `lore map [owner/repo] [--json]` is a hidden command, like `lore codemap` and `lore glossary`.
- `lore map check` validates the format: required fields, `pin` values, and that each edge
  names repos the map lists. It exits 1 on an error. The check does not verify that an edge is
  true.

### Keeping the map true

- `lore map scan [--scope <folder>] --json` collects candidate edges from files alone. It reads
  git submodules, git URLs and package names in `pyproject.toml` and `package.json`, `FROM`
  images in Dockerfiles and images in `docker-compose` files. It maps each candidate to an
  `owner/repo` where the scope's checkouts allow. It calls no model and no network.
- The `lore:map` skill, in the `lore` plugin, has two phases.
  - **Map phase.** Run the scan. Read the code around each candidate. Fill `interface` and
    `breaks_when`. Compare with the map. Open one wiki PR with three sections: added, changed,
    and edges the scan no longer finds. The skill removes no edge itself; a human deletes it in
    the PR. A repo in the map without a local checkout is reported, not removed.
  - **Tidy phase.** Runs only after the map PR merged. In each repo's `AGENTS.md` or
    `CLAUDE.md`, remove text that describes another repo, and only where the map now holds the
    fact. Add one line that points to the map. Keep the repo's own rules: commands,
    conventions, release steps. Borderline text, such as a working order across repos, moves
    into the edge or the map's prose and is named in the PR body. When in doubt, the text
    stays. One PR per repo.
- A scope can switch the tidy phase off in the person's config (`scopes.<name>.map.tidy:
  false`), for repos that colleagues without Lore read.
- The SessionStart banner shows a hint when the scan finds a candidate the map lacks. The scan
  runs in a detached background child, like the codemap refresh. Nothing runs the skill
  automatically.

### Skills that read the map

- `orient`, full mode: the cross-repo explorer starts from the current repo's edges.
- `to-epic`: a cross-repo row of the build order names the edge's `pin`.
- `build`, `epic` mode: the cross-repo pin step reads the `pin` field.

### Decision record

ADR 0014 (one home per fact) gets an amendment: the system map is the home for "what spans several repos". No new
ADR. The edge format is the part that is hard to reverse, and this PRD pins it.

## Testing decisions

- Parser: both required and optional fields, a map with an unknown `pin`, a repo in two maps,
  a note without the `system-map` type.
- SessionStart: the neighbours block for a consumer and for a producer, the character budget,
  no block without an edge, the block before the glossary index.
- `lore map check`: each error names the note and the edge, and exits 1.
- `lore map scan`: fixture checkouts with a submodule, a `pyproject.toml` git dependency, a
  `package.json` dependency, a Dockerfile `FROM` and a compose image. Each candidate maps to
  the right `owner/repo`.
- Skill text: grep tests in the style of `tests/test_workflow_build_skill.py`. They pin the
  map-first order, the PR-only writes, the keep-when-in-doubt rule and the tidy switch.
- Prior art: `tests/test_glossary.py`, `tests/test_hooks_ledger_breakpoints.py`,
  `tests/test_hooks_codemap_gate.py`.

## Out of scope

- Checking edges against dependency files after the map exists. That comes later, as a
  roadmap item.
- Raising `lore workflow risk` to `high` when a PR touches an interface named in
  `breaks_when`. Later.
- Open PRs and epics in neighbour repos at SessionStart. Later.
- A generated neighbours block inside `AGENTS.md`. It would put the edges in two homes.
- Edges inside one repo.
