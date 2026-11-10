# Lore

**A workflow and knowledge layer for teams that code with Claude Code.**
Each session starts with a small, repo-scoped context. It follows one
workflow from idea to merged PR. It files every fact where someone will
read it: issues, PRs, ADRs and PRDs, a git-backed team wiki, a logbook.
Lore itself calls no model. The agent in the session does the judging.

> ⚠️ **Pre-1.0.** APIs, hook contracts, skill surfaces, frontmatter
> schema, and CLI flags can still change between minor versions.

## How it works

![Lore architecture](docs/img/architecture.svg)

- **Session.** At SessionStart, Lore injects at most 5,000 characters of
  context: the repo's orientation note, system-map neighbours, the glossary
  index, a recap of the last active day, a handover note if one is waiting,
  and pending freshness verdicts. Everything deeper is **pulled on demand
  over MCP** (`lore_search`, `lore_read`, `lore_context_pack`, …).
- **This machine.** Hooks register every transcript in a ledger, stamped
  with repo, branch, PRs, issues, commits and files from git. No model
  call is involved. Handover notes and token records also stay local.
- **The repository.** The session files each fact as the artifact that
  already has a reader: an issue, a comment, a PR, an ADR or PRD,
  `CLAUDE.md` / `CONTEXT.md`, docs. See
  [file facts as artifacts](docs/how-to/file-facts-as-artifacts.md).
- **Team wiki.** A markdown wiki in its own git repo: projects, concepts,
  the orientation note per repo, and system maps across repos. Notes
  arrive by PR or `/lore:inbox`. Lore never writes a wiki on its own.
- **Logbook.** Every finish point writes a logbook entry to a configured
  sink: a file in the repo, or a command such as a project tracker. See
  [use the logbook](docs/how-to/use-the-logbook.md).
- **Colleagues** work on the same artifacts and the same wiki, with the
  same workflow.

## Two plugins: `lore` + `lore-workflow`

This repo is a Claude Code marketplace with two plugins, versioned and
installed independently:

| Plugin | What it's for | Depends on |
|---|---|---|
| **`lore`** | Hooks, MCP server, and the `/lore:*` skills: capture, context, search, wiki upkeep. | nothing else |
| **`lore-workflow`** | One planning-and-build workflow: orient, grilling, epics, PRDs, TDD, docs. | `lore` |

The `lore-workflow` chain:

```
orient → grilling → to-epic → build → document     (+ handover at any stop)
```

`build` runs in three modes: `loop` for fast rounds with you watching,
`issue` for one clear issue, `epic` for a build order of sub-issues. `tdd`
is the discipline every mode follows; `debug`, `triage` and `file-issue`
are helpers. See [`lore-workflow/README.md`](lore-workflow/README.md).

The `lore` skills:

| Skill | What it does |
|---|---|
| `/lore:context` | Show what Lore injected this session. |
| `/lore:handover` | Write a handover note for the next session. |
| `/lore:inbox` | Route inbox files into the right wiki. |
| `/lore:map` | Keep the system map true across repos. |
| `/lore:system` | Bring every checkout of a system to its target branch. |
| `/lore:verify` | Resolve pending freshness verdicts. |
| `/lore:curator` | Frontmatter hygiene: supersession, back-links, dates. |

## Install

Linux and macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/buchbend/lore/main/install.sh | sh
```

The script installs the `lore` CLI with `pipx`, `uv tool` or `pip --user`,
then runs `lore setup`. Setup creates or links a wiki and installs both
Claude Code plugins, plus Cursor's MCP wiring where present. It offers a
scope for the current repo and runs the checks. Upgrade later with `lore update`.

By hand:

```bash
pipx install "git+https://github.com/buchbend/lore.git#egg=lore"
lore setup
```

> The bare `pipx install lore` will *not* work: the name is taken on PyPI
> by an unrelated package.

Plugins only, from inside Claude Code (you still need the CLI):

```text
/plugin marketplace add buchbend/lore
/plugin install lore@lore
/plugin install lore-workflow@lore
```

`lore setup` is also the repair command. On a healthy setup it writes
nothing; `lore setup --yes` runs without a terminal. `lore uninstall`
removes everything Lore added. For an editable or offline install, see
[CONTRIBUTING.md](./CONTRIBUTING.md).

## Configure

**Vault layout.** `$LORE_ROOT` (default `~/lore`) holds `wiki/` with one or
more mounted wikis, an optional `inbox/`, and a `CLAUDE.md`. Each wiki is
its own git repo, mounted as a symlink or an inline directory:

```bash
ln -s ~/git/myorg/team-knowledge ~/lore/wiki/team   # mount an existing wiki
lore wiki new research                              # or scaffold a new one
```

**Scopes.** A scope maps a folder of repos to a wiki, in
`~/.config/lore/config.yml`. Every repo below that folder uses the wiki.
A committed `.lore.yml` in a repo holds exceptions only.

```bash
lore setup scope ~/git/myorg     # or: --name myorg --wiki team --remote 'github.com/myorg/*'
```

**Sources and sinks.** `lore setup source` adds search sources beyond the
wiki and GitHub (DokuWiki, any command). `lore setup sink` sets where
logbook entries go. `lore config` shows the resolved configuration and
where each value comes from; `lore config secret` stores tokens.

Guides: [onboarding](docs/how-to/onboarding.md),
[route repos to a wiki](docs/how-to/route-repos-to-a-wiki.md),
[onboard a repo](docs/how-to/onboard-a-repo.md),
[keep a system map](docs/how-to/keep-a-system-map.md).

## Daily use

<<<<<<< HEAD
| Command | What it does |
=======
### Uninstall

```bash
lore uninstall                  # symmetric remove
```

Removes the entries Lore added — including from shared JSON files like
`~/.cursor/mcp.json`. Other servers / your own edits outside Lore-managed
markers stay put.

### Migrating from the pre-v0.10 `install.sh`

> Only relevant if you ran a `lore` `install.sh` from before v0.10 — the
> one that wrote skill symlinks directly into `~/.claude/skills/`. The
> current `install.sh` is the thin bootstrap installer documented in
> § Install above; it never writes those symlinks.

If you ran the pre-v0.10 bash installer, `lore setup` will refuse
with a clear warning until you reset:

```bash
git clone https://github.com/buchbend/lore.git    # if you don't have a checkout
cd lore
python3 tools/undo_install_sh.py --dry-run        # preview what would change
python3 tools/undo_install_sh.py                  # apply
curl -fsSL https://raw.githubusercontent.com/buchbend/lore/main/install.sh | sh
```

The undo helper is stdlib-only Python; runs even if `lore` isn't on
your PATH yet.

### As a Claude Code plugin (via marketplace)

The repo is a self-describing marketplace:

```
/plugin marketplace add buchbend/lore
/plugin install lore@lore
```

That alone gives you the `lore` plugin (hooks, skills, subagents, MCP); add
`/plugin install lore-workflow@lore` for the planning-chain skills too — see
[§ Two plugins](#two-plugins-lore--lore-workflow). Installing `lore` alone
does not install the `lore` CLI itself. Run
`pipx install "git+https://github.com/buchbend/lore.git#egg=lore"`
separately, or run `lore setup` once `lore` is on your PATH (it'll
subprocess `claude plugin install lore@lore` for you).

### Dev install (editable, also the offline / air-gapped path)

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the editable-from-checkout
recipe. Same recipe is the path for installs on machines without
network egress to PyPI / the marketplace.

## Bootstrap: passive capture

Capture is automatic and needs no explicit command. Every transcript
Claude Code produces is registered into the transcript ledger and
mirrored into the wiki's `.transcripts/`, stamped with a linkage block
(repo, branch, PRs, issues, commits, files) derived from git state — no
LLM call, no prose (see `CONTEXT.md` for the full model). Capture
writes nothing into a wiki itself. Everything in a wiki — concepts,
decisions, projects, reference notes — is written directly, by hand, via
`/lore:inbox`, or through a pull request an agent opens; there is no
automatic daily abstraction pass.

### Update from an older install

One command — upgrades the binary, refreshes integrations, and pokes
Claude's plugin cache:

```bash
lore update
```

It delegates to `install.sh upgrade`, which picks your installer
(pipx / uv / pip), pulls the latest `lore`, then refreshes the Claude
plugin cache so hooks/skills/MCP stay in sync. Re-running
the curl one-liner from § Install does the same thing if you don't
have `lore` on PATH for any reason.

For an editable dev checkout:

```bash
cd /path/to/your/lore-checkout
git pull origin main
pip install -e .
lore setup
```

(Fresh installs follow [§ Install](#install) above.)

### Route repos to a wiki — one scope per folder

A **scope** is a folder in your `~/.config/lore/config.yml`. Every repo
below it gets the scope's wiki; a new repo there needs no step. Name the
folder, or run the command in it:

```bash
lore setup scope ~/git/ccatobs   # or: --name ccat --wiki ccat --remote 'github.com/ccatobs/*'
```

Inside a repo, without a path, `lore setup scope` offers the folder above the repo.

A committed `.lore.yml` in a repo holds exceptions only, such as a
different wiki.

### What runs automatically

Once a scope routes the repo to a wiki:

- **Claude Code hooks register every transcript into the ledger.**
  SessionStart, PreCompact, SessionEnd, and a mid-session
  UserPromptSubmit heartbeat all route through the same registration
  path (`lore_curator/capture_routing.py`): each upserts a ledger entry
  and stamps its linkage block, and a session boundary promotes that
  stamp to a full transcript read (edited files, commit SHAs). No LLM
  call; registration is the end of the capture path, not a step toward
  composing anything.
- **SessionStart also spawns a detached transcript sync**, mirroring
  every routed transcript into its wiki's `.transcripts/` and
  emitting the one live drain event, `transcript-synced`. An
  opportunistic, flock-guarded retention sweep runs in-process
  alongside it, short enough not to block SessionStart. The transcript
  sync, the wiki pull and the codemap refresh run as detached children.
- **SessionStart also spawns two more detached children**, so a slow
  remote or a large repo never delays the banner. `lore hook wiki-pull`
  fetches and fast-forwards the routed wiki. `lore hook codemap-refresh`
  refreshes `CODEMAP.md`. Both are hidden verbs that run in the
  background. A dirty-tree or diverged-history warning from the pull
  shows in the banner one session later, because the child records it
  after the banner is drawn.
- **Banner at SessionStart** is deliberately minimal: a status line, an
  optional Focus block, a last-active-day recap read off the transcript
  ledger (day, session count, repos, branches, refs — no LLM call),
  freshness lines only on positive evidence, a fixed directive pointing
  at MCP pull for anything deeper, and the index. The index names what
  the repo holds: the glossary terms, the ADR titles, the current PRDs,
  the roadmap headings and the newest lab entries. `lore index` prints
  it. `lore!:` prefix flags actionable errors.

### Search

`lore_search` (MCP) is a federated search. It returns two lists: ranked
wiki notes from the local index, then issues and PRs from one live GitHub
search. Nothing about an artifact is stored. When `gh` cannot answer, the
result holds the wiki list and names the omission. See
[`docs/how-to/search-notes-and-issues.md`](docs/how-to/search-notes-and-issues.md).
The context pack carries the body of each focus issue, so the common case
needs no search.

### Manual escape hatches

- `lore ingest --from <file.jsonl> --integration cursor --directory <cwd>` —
  ingest a transcript from any integration lore doesn't auto-capture.
- `lore wiki curate [--wiki <name>] [--apply]` — the frontmatter-only
  hygiene pass (supersession, `implements:` back-links, git-date
  backfill, team-mode hint); dry-run by default.
- `lore status` — what applies in the current repo (wiki, scope,
  sinks) and where each value comes from.

### Per-wiki configuration

Each wiki can set its own knobs in `<wiki>/.lore-wiki.yml`:

```yaml
git:
  auto_push: true                # true by default when the wiki has a remote
  auto_pull: true
breadcrumb:
  mode: normal                  # quiet | normal | verbose
  scope_filter: true
```

All fields default to sane values — start without a `.lore-wiki.yml`
and add knobs only as you need them.

## Observability

Every background producer (hooks, the hygiene curator, transcript sync,
the retention janitor) writes one envelope onto one append-only
event log, the **spine**. Each envelope carries a `trace_id` field for
correlating several records into one story.
Three commands cover the common scenarios:

| Scenario | Command |
>>>>>>> origin/main
|---|---|
| `lore status` | Health: capture, wikis, retention, alerts. Run it first when something seems off. |
| `lore stats` | Token-use and systems pages in the browser. |
| `lore search` / `lore read` | Search the wiki index; composite retrieval. The same as the MCP tools. |
| `lore log add\|flush\|show` | Write, retry or show logbook entries. |
| `lore roadmap add` | Add an idea to the roadmap. |
| `lore system status\|target` | Git state of every repo in a system; its target branch. |
| `lore trace tokens <session>` | Token totals per skill or subagent phase. |
| `lore update` | Upgrade the CLI and plugins. |

Event logs live in `$LORE_ROOT/.lore/spine.jsonl`. See
[observability](docs/architecture/observability.md) and
[troubleshooting](docs/how-to/troubleshooting.md).

## Design principles

- **Markdown and git stay authoritative.** No database the vault cannot be
  rebuilt from.
- **One home per fact.** Each fact lives in the artifact that already has a
  reader; nothing is extracted from transcripts behind your back.
- **Small context up front, the rest on demand.** Bounded, deterministic
  context at SessionStart; the agent pulls more over MCP when it needs it.
  No auto-enrichment on every tool call.
- **No model call in Lore.** Hooks, capture and curation are deterministic.
  The agent in the session does the judging.

## Documentation

- [`docs/how-to/`](docs/how-to/): task recipes (run an epic, use the fast
  path, hand over a session, keep a roadmap, record a decision, …).
- [`docs/explanation/`](docs/explanation/): why the design is as it is.
- [`docs/architecture/`](docs/architecture/): internals (CLI contract,
  config, ledger, state, sync, observability).
- [`docs/conventions.md`](docs/conventions.md): the full chain, where each
  fact lives, the model-tier vocabulary.

## Credits

The grilling method, the shared-language glossary (`CONTEXT.md`), the ADR format and the
TDD skill in `lore-workflow` come from Matt Pocock's
[`mattpocock/skills`](https://github.com/mattpocock/skills) (MIT). The step from a
conversation to a PRD and issues is his idea too. The rest of `lore` and `lore-workflow`
is our own. [lore-workflow/THIRD-PARTY.md](./lore-workflow/THIRD-PARTY.md) says exactly
which parts are his, what we changed, and holds his licence.

## License

MIT. See [LICENSE](./LICENSE).
