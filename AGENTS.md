## Where to look
Decisions: `docs/adr/`. Specs: `docs/prd/`. Terms: `CONTEXT.md`. Plan: `ROADMAP.md`.
With Lore: `lore index` lists all of them.

## Releasing

Every merge to `main` that ships plugin-relevant behavior (hooks, MCP server,
curator/capture code, skills) **must** carry a version bump. The shipping PR
holds the bump as its last commit. Add the bump after the merge approval,
immediately before the merge. Merge `main` into the PR branch first. Then run
`python3 tools/release.py --in-branch --notes notes.md` on the PR branch. The
script bumps `.claude-plugin/plugin.json`, `pyproject.toml` and `CHANGELOG.md`
together, runs the version-sync guard, and commits `chore: release X.Y.Z` on the
current branch. The script creates no branch, pushes nothing and opens no PR.

A review fix never lands after the bump, so no bump is redone. Two open PRs do
not conflict on `CHANGELOG.md`: the second PR bumps on top of the first PR's
release.

`python3 tools/release.py --notes notes.md` (no `--in-branch`) still cuts a
separate release PR. Use the separate PR when a merged change shipped without
a bump.
A squash merge of a PR with an in-branch bump loses the `chore: release` subject.
The separate mode uses that subject to find its range. After such a merge, pass
`--notes` to the separate mode.

`main` is branch-protected: the `test` check must pass, and direct pushes are
blocked (admins included). `claude plugin update lore@lore`
only re-fetches on a version *change*; without the bump, installed plugin
caches silently stay on the old code even though `main` has moved on (this bit
us once already — see `CHANGELOG.md`'s own header note and commit `004d033`).
