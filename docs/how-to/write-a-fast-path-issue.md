# Write a good fast-path issue

**Goal:** write a GitHub issue that the [fast path](use-the-fast-path.md)
(`/lore-workflow:implement-issue`) can pick up and implement directly, with
no back-and-forth.

The fast path reads the issue and the code map, then — only if the issue is
ambiguous — asks **at most three** clarifying questions before it starts. A
well-written issue spends none of that budget: the skill reads it, locates
the symbols in `CODEMAP.md`, and goes straight to test-first implementation.
A vague one either burns the three questions or, worse, ships the wrong
thing.

## What a good fast-path issue contains

- **One change, clearly scoped.** The fast path is one issue, one branch,
  one pull request. If the issue describes several features, it belongs on
  the [epic chain](run-an-epic.md), not here. Split it.
- **Unambiguous intent.** State what should change and why in plain
  sentences. The reader should not have to guess the goal.
- **Concrete acceptance criteria.** List what "done" means as checkable
  statements — the behaviour a test could assert. These become the
  test-first targets; without them the skill has to invent them.
- **Pointers into the code.** Name the command, module, or symbol the
  change touches. You do not need file-and-line precision — the code map
  resolves symbols — but naming the entry point removes a whole class of
  ambiguity.
- **Out-of-scope notes where they help.** If there is an obvious adjacent
  change you do *not* want, say so. It keeps the single pull request tight.

## What to leave out

- **A design interview.** The fast path is not a `grilling` session; do not
  write the issue as an open design question. If the shape is still
  unsettled, shape it on the chain first.
- **Multi-repo or multi-feature scope.** That is epic-shaped work.

## Checklist

- [ ] One change, small and clear.
- [ ] Intent stated plainly.
- [ ] Acceptance criteria a test could check.
- [ ] The touched command / module / symbol named.
- [ ] Anything deliberately out of scope called out.

## Done when

Someone who has never seen the change could read the issue and know exactly
what to build and how to tell it works — which is precisely what the fast
path needs.
