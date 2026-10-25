---
name: lore:map
description: Keep the system map true. The map phase scans the local checkouts
  of a scope, compares the candidate edges with the wiki's system map and
  opens one wiki PR. The tidy phase runs after that PR merged and removes
  cross-repo text from each repo's AGENTS.md or CLAUDE.md, one PR per repo.
  Run with "/lore:map".
user_invocable: true
---

# System map

A system map is a wiki note at `<wiki>/systems/<name>.md` with `type: system-map`. It lists
the repos of one system and the edges between them (PRD 0019, the system map). Each edge names
`consumer`, `producer` and `interface`, plus the optional `pin` (`sha`, `tag`, `image`,
`package` or `none`) and `breaks_when`. Agents read the frontmatter. The body is prose for
humans.

The skill proposes. Humans merge. Every write goes through a PR. Never push to a default
branch, and never merge a PR yourself.

## Map phase

1. Run `lore map scan --json` from inside the scope folder. Pass `--scope <folder>` when the
   cwd is outside it. The output holds `folder`, `scope`, `tidy`, `checkouts`, `candidates`,
   `missing` and `no_checkout`.
2. Run `lore map --json` and `lore map check`. Fix a format error the check names in the same
   PR.
3. For each entry of `missing`, read the code around its `source` in the consumer's checkout.
   Find what the consumer reads from the producer. Fill `interface` with that, in a few words.
   Fill `breaks_when` with the producer change that breaks the consumer. Take `pin` from the
   candidate. A candidate that is no real dependency (a test fixture, a dead file) stays out.
   Name it in the PR body.
4. For each existing edge, check that the scan still finds it. An edge with a changed `pin`
   or `interface` goes to the changed section.
5. Leave each edge the scan no longer finds in the map. A human deletes it in the PR. List it
   in the third section with the reason you see.
6. For each repo in `no_checkout`, report the repo in the PR body and keep its edges. A scan
   without the checkout knows nothing about the repo.
7. Write the change into `<wiki>/systems/<name>.md`. A new system gets a new note, with every
   repo of its edges in `repos`. Run `lore map check` again; it exits 0 before you push.
8. Open one wiki PR per run on a branch of the wiki repo. The body holds three sections:

   ```markdown
   ### Added
   - <consumer> <- <producer>: <interface> (found in <source>)
   ### Changed
   - <consumer> <- <producer>: <old> → <new>
   ### No longer found
   - <consumer> <- <producer>: <what the scan missed>
   ```

   Write an empty section as `- none`. Run `lore style show writing-rules` before you write
   the body.

## Tidy phase

While the map PR is open, do not start the tidy phase. Start it in a later run, after a human
merged the map PR.

Where the scan output's `tidy` is `false`, skip the tidy phase for that scope. The person sets
`scopes.<name>.map.tidy: false` in their config for repos that colleagues without Lore read.

For each repo of the map with a local checkout:

1. Read its `AGENTS.md`, or `CLAUDE.md` when no `AGENTS.md` exists. `lore setup` turns
   `CLAUDE.md` into an `@AGENTS.md` import, so the edit usually lands in `AGENTS.md`.
2. Find text that describes another repo: what it provides, how this repo pins it, what
   breaks when it changes. Remove the text only where the map holds the same fact.
3. Keep the repo's commands, conventions and release steps. If a passage may be a repo rule,
   keep the passage.
4. Borderline text, such as a working order across repos, moves into the edge or the map's
   prose. That move is a wiki PR of its own. Name each kept borderline passage in the PR body.
5. Add one line that links the map: `System map: <wiki>/systems/<name>.md (lore map
   <owner/repo>)`.
6. One PR per repo. Each PR body lists the removed passages and the map edge that holds each
   one.

## Report

List each PR you opened, each repo in `no_checkout`, and each candidate you left out with its
reason.
