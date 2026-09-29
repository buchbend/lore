## Lore

<!-- Managed by /lore:attach. Safe to edit — changes are preserved on re-run. -->

- wiki: private
- scope: lore
- backend: github

## Releasing

Every merge to `main` that ships plugin-relevant behavior (hooks, MCP server,
curator/capture code, skills) **must** carry a version bump. The shipping PR
holds the bump as its last commit. Run
`python3 tools/release.py --in-branch --notes notes.md` on the PR branch. The
script bumps `.claude-plugin/plugin.json`, `pyproject.toml` and `CHANGELOG.md`
together, runs the version-sync guard, and commits `chore: release X.Y.Z` on the
current branch. It creates no branch, pushes nothing and opens no PR.

Two open PRs that both bump conflict on `CHANGELOG.md`. The second PR merges
`main` and reruns `--in-branch`. Drop its earlier bump commit first.

`python3 tools/release.py --notes notes.md` (no `--in-branch`) still cuts a
separate release PR. Use it when a merged change shipped without a bump.

`main` is branch-protected: the `test` check must pass, and direct pushes are
blocked (admins included). `claude plugin update lore@lore`
only re-fetches on a version *change*; without the bump, installed plugin
caches silently stay on the old code even though `main` has moved on (this bit
us once already — see `CHANGELOG.md`'s own header note and commit `004d033`).
