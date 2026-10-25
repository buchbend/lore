# Mode `loop`

Read this when `build` runs in `loop` mode. The rules in [SKILL.md](SKILL.md) apply too.

Small asks, the human watching. No PRD, no teammates, no PR per round.

**Stop and advise design** before you code an ask with any of these signs:
- a new data model, table, API endpoint or persisted state;
- several features or component boundaries;
- a choice between real alternatives the user has not made;
- auth, permissions, secrets or other users' data;
- no way to state it as one testable behaviour.

Name the sign in one line. Recommend
`orient` then `issue`, or `grilling` then `to-epic`. Log the ask as a `left` line and carry on
with the small asks. The user may say "do it anyway"; then slice it into small rounds.

**Setup, once.**
1. The target is the branch the dev stack serves: the branch of the primary checkout (`git
   worktree list`, first row). Only `develop` or `main` qualify. Anything else, or both look
   plausible: ask.
2. Create branch `loop/<slug>` off the target in a sibling worktree. Edit only there.
3. Start the ledger in the worktree: `lore workflow ledger-add --kind resume --text "base <sha>
   of <target>" --next "round 1"`.

**One round.**
1. Restate the asks, one line each. Pick and name a default for an ambiguous ask. Ask only when
   a wrong guess is costly to undo.
2. Read the code each ask touches, shared helpers too. A helper that rebuilds a record from
   known fields drops a new field; grep its callers.
3. Test first where there is behaviour: a handler, a state change, an API field, a key path, a
   phone-width branch. Assert a CSS rule that carries behaviour. Pure looks get no test; list
   them as "visual only".
4. Run only this round's tests, plus ruff on the touched files. Read the result, then commit.
   Do not chain the commit behind the test command. One commit per round.
5. Merge into the target locally: in the primary checkout, `git merge --ff-only loop/<slug>`.
   A dirty checkout or a non-fast-forward: stop and ask. Restart the dev stack the way the repo
   says. Nothing reaches the remote until wrap-up.
6. Spawn one background subagent at the `cheap` tier. It runs the full suite and ruff on the
   merged commit, read-only, and reports failing test ids or "green". A new round replaces a
   running check. A failure becomes the first ask of the next round.
7. Add ledger lines for this round's candidates, then the resume line (breakpoint).
8. Report: what changed, what the tests verify, what is visual only, the defaults you picked,
   new ledger lines, the last background result. Ask for the next round.

**Traps.** A value read back right after a router write is still the old value; keep the new
one in a local. A leave transition removes its element a frame later. A test that stubs globals
restores them on failure too. Without a browser the look is unverified; say so.

On "wrap up", read [loop-wrap-up.md](loop-wrap-up.md) and follow it.
