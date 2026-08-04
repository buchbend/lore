## Lore

<!-- Managed by /lore:attach. Safe to edit — changes are preserved on re-run. -->

- wiki: private
- scope: lore
- backend: github
- issues: --assignee @me --state open
- prs: --author @me

## Releasing

Every merge to `main` that ships plugin-relevant behavior (hooks, MCP server,
curator/capture code, skills) **must** end with a version bump — bump
`.claude-plugin/plugin.json`, `pyproject.toml`, and `CHANGELOG.md` together in
one `chore: release X.Y.Z` commit, landed via its own PR. `main` is
branch-protected: the `test` check must pass, and direct pushes are blocked
(admins included). `claude plugin update lore@lore`
only re-fetches on a version *change*; without the bump, installed plugin
caches silently stay on the old code even though `main` has moved on (this bit
us once already — see `CHANGELOG.md`'s own header note and commit `004d033`).
