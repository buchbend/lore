# Write a good fast-path issue

**Goal:** write a GitHub issue that the [fast path](use-the-fast-path.md)
(`/lore-workflow:implement-issue`) can pick up and implement directly, with
no back-and-forth.

Run `lore style show issue-register` and follow its section skeleton and EARS
acceptance criteria. The register is the source of truth for issue-body
prose; the points below are what the fast path additionally needs.

The fast path reads the issue and the code map, then — only if the issue is
ambiguous — asks **at most three** clarifying questions before it starts. A
well-written issue spends none of that budget: the skill reads it, locates
the symbols in `CODEMAP.md`, and goes straight to test-first implementation.
A vague one either burns the three questions or, worse, ships the wrong
thing.

## What the fast path adds to the register

- **One change, clearly scoped.** The fast path is one issue, one branch,
  one pull request. If the issue describes several features, it belongs on
  the [epic chain](run-an-epic.md), not here. Split it.
- **Pointers into the code.** Under "References", name the command, module,
  or symbol the change touches. You do not need file-and-line precision —
  the code map resolves symbols — but naming the entry point removes a whole
  class of ambiguity.

## What to leave out

- **A design interview.** The fast path is not a `grilling` session; do not
  write the issue as an open design question. If the shape is still
  unsettled, shape it on the chain first.
- **Multi-repo or multi-feature scope.** That is epic-shaped work.

## Checklist

- [ ] The register's sections filled, acceptance criteria in EARS.
- [ ] One change, small and clear.
- [ ] The touched command / module / symbol named under "References".
- [ ] Nothing in it that belongs on the epic chain.

## Done when

Someone who has never seen the change could read the issue and know exactly
what to build and how to tell it works — which is precisely what the fast
path needs.
