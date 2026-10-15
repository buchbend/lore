# Keep a roadmap

**Goal:** write down where the code is heading, and in what order, in one file per repo.

A **roadmap** is the `ROADMAP.md` file in a repo (PRD 0018, the roadmap). A
component directory of a monorepo can hold its own `ROADMAP.md`. The file at
the repo root is the roadmap of the repo. The workflow skills read the file,
link plans to its items and remove shipped items.

## The shape of the file

```markdown
# Roadmap

One paragraph: where the code is heading.

- **Item title.** What and why, in one paragraph.
  Links: PRD, epic, issue · milestone pm:<id>

## Not placed yet

- **Item title.** An idea without a place in the order.
```

- The order of the top list is the intended order.
- Write no dates, quarters or owners. A milestone holds the date. The PRD or the milestone names the owner.
- An item with an issue links to the issue. The item does not restate the issue text.
- Links go down, from the item to its PRD, epic or issue. A PRD holds no link up to the item.

## File an idea

```
lore roadmap add "Faster search" "Cache the ranked hits per query."
```

- The command appends `- **Faster search.** Cache the ranked hits per query.` under `## Not placed yet`.
- The command writes to the nearest `ROADMAP.md` between the working directory and the repo root.
- Without a `ROADMAP.md`, the command creates the file at the repo root: the `# Roadmap` heading, a placeholder paragraph and the `## Not placed yet` section.
- Without a `## Not placed yet` heading, the command adds the heading at the end of the file.
- The command writes the working tree only. Commit the change on your branch. The next pull request carries it.
- Outside a git repo, the command exits 1 and prints one line.

## Place an item

Move the item from `## Not placed yet` into the top list, at its place in the
order. Edit the file by hand. A dropped item moves back down to
`## Not placed yet`.

## Plan and build from an item

- `orient` reads the roadmap first. Name an item, and `orient` takes the item as its seed.
- `grilling` asks which item a settled plan serves.
- `to-epic` adds the PRD and epic links to the item.
- `build` removes the shipped item at its finish point, on the build branch. `CHANGELOG.md` and the shipped PRD keep the record.
- An agent files a feature idea outside its task with `lore roadmap add`, through its pull request.
- `handover` files a deferred feature idea the same way, on the open branch of the work. An idea with findings that a cold session would rediscover also gets a seed issue, and the item links the seed. Without an open branch, the seed holds the idea alone, and `to-epic` adds the item later.

## Name a milestone

Write the milestone as a ref, for example `pm:CCAT-K7QX2M`. Where a source
serves the ref, `orient` reads the ref with `lore_read`. Without a source,
the ref stays plain text. ADR 0018 (Lore and project-manage meet only through
generic contracts) holds the decision.

## A roadmap across repos

A roadmap that spans several repos is a wiki topic note, not a `ROADMAP.md`
(ADR 0014, one home per fact).
