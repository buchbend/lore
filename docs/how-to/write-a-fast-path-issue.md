# Write a good fast-path issue

**Goal:** write a GitHub issue that the [fast path](use-the-fast-path.md)
(`/lore-workflow:implement-issue`) can pick up and implement directly, with
no back-and-forth.

Run `lore style show writing-rules` and follow its section skeleton and EARS
acceptance criteria. The rules are the source of truth for issue-body
prose; the points below are what the fast path additionally needs.

Open the repo's `CONTEXT.md` as well. The glossary gives each domain term one
meaning, and rule 20 asks every short name to carry an entry. Draft with those
terms rather than a synonym. Where a term you need has no entry, write the
meaning out in full and ask for a `grilling` session to add the term. Never
append to `CONTEXT.md` yourself. A repo that holds no `CONTEXT.md` never blocks
your draft; the fast path files the issue and names the absence.

The fast path reads the issue and the code map, then — only if the issue is
ambiguous — asks **at most three** clarifying questions before it starts. A
well-written issue spends none of that budget: the skill reads it, locates
the symbols in `CODEMAP.md`, and goes straight to test-first implementation.
A vague one either burns the three questions or, worse, ships the wrong
thing.

## What the fast path adds to the writing rules

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
- **A short name for a piece of work.** Rule 21 keeps a phase name, a group
  name and a priority code out of the title and the body. Cite the issue
  number instead. Not "the G4 group" but "issue 412".

## Checklist

- [ ] The sections the writing rules require filled, acceptance criteria in EARS.
- [ ] One change, small and clear.
- [ ] Every domain term taken from `CONTEXT.md`, every short name defined there.
- [ ] No phase name, group name or priority code anywhere in the text.
- [ ] The touched command / module / symbol named under "References".
- [ ] Nothing in it that belongs on the epic chain.

## Done when

Someone who has never seen the change reads the issue and knows what to build.
The reader also knows how to tell the change works. The fast path needs both.
