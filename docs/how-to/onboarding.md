# Onboarding: install → setup → verify

**Goal:** go from a fresh machine to "Lore routes this repo to a wiki" in
one guided pass, and know how to tell it worked.

`lore setup` is the one command for first run, for adding a wiki or a
scope, and for repair (PRD 0016). A second run on a healthy setup writes
no file and prints one check mark per step.

## Before you start

- A terminal with `curl` and `python3` available.
- A GitHub (or other git host) URL if you're joining an existing **team**
  wiki; nothing extra if you're starting a **personal** one.

## Steps

1. **Install the binary.**

   ```
   curl -fsSL https://raw.githubusercontent.com/buchbend/lore/main/install.sh | sh
   ```

   The installer exits non-zero if the installed `lore` isn't actually
   runnable on `PATH` afterward. On a first install it runs `lore setup`.

   Later on, run `lore update` to pick up a new release (checks the
   version on `main`, then upgrades the package and refreshes the Claude
   plugin cache in one step; `--check` reports without upgrading).

2. **Run setup** (the installer already did, on a first install).

   ```
   lore setup
   ```

   Four steps, each one line:

   | Step | What it does |
   |---|---|
   | wiki | Picks the vault (`$LORE_ROOT`, the configured `lore_root`, or `~/lore`), then creates a personal wiki, clones a team wiki or links a folder. |
   | agents | Installs the Claude Code plugin, or refreshes a stale plugin cache, repairs Cursor's wiring where Cursor is present, and installs the OpenCode plugin where OpenCode is present ([use Lore in OpenCode](use-lore-in-opencode.md)). Once the `lore` plugin is current, the step uninstalls the old plugin `lore-workflow@lore`. |
   | scope | In a repo no scope covers, offers the folder above the repo as a scope: every repo below it routes to the scope's wiki. |
   | check | Runs the install checks; a failing advisory check does not fail setup. |

   Setup also migrates the old `attachments.json`, `scopes.json` and
   `$LORE_ROOT/.lore/config.yml` into `~/.config/lore/config.yml`, then
   deletes them.

   Every prompt has a flag; without a terminal, or with `--yes`, every
   answer comes from flags and defaults:

   ```
   lore setup --yes --wiki-new personal
   lore setup --yes --wiki-clone git@github.com:you/team-wiki.git
   lore setup --yes --vault ~/lore --wiki-link ~/existing-wiki
   lore setup scope --yes --name ccat --wiki ccat --remote 'github.com/ccatobs/*'
   ```

   `lore setup wiki` and `lore setup scope` run one step.

   A run that changes something ends with the output of `lore config`.

3. **Restart Claude Code.** Hooks and skills are picked up at session
   start.

4. **Verify.** From a repo inside a scope:

   ```
   lore config
   lore status
   ```

   `config` shows how everything is wired: wikis, scopes, defaults, sinks,
   sources, and the route of this repo with the origin of each value. A
   `!` marks a line that needs a look. `status` shows whether a session has
   captured anything yet. A fresh install with no session activity yet
   shows "no activity" lines, not errors.

## Join a team wiki

A colleague's folder layout differs from yours, so a team routes by git
remote. Clone the team wiki, then add a scope for the team's remotes:

```
lore setup --wiki-clone git@github.com:ccatobs/ccat-knowledge.git
lore setup scope --remote-only --wiki ccat-knowledge --remote 'github.com/ccatobs/*'
```

Skip the second command when the wiki declares its remotes in its
`.lore-wiki.yml` (`routing.remote`): then cloning the wiki is the whole
setup. Run `lore config` to see the result. Add a logbook sink with
`lore setup sink` only if the team uses one: sinks are personal and never
come from the wiki (ADR 0016).

## Keep your setup on more than one machine

- **The config.** `~/.config/lore/config.yml` holds no secrets, so it can
  live in a dotfiles repo. A symlink to the dotfiles copy is safe: Lore
  writes through the link. Remote-only scopes need no folder paths, so the
  file fits every machine. Do not link `secrets.yml`: Lore refuses a
  secrets file inside a git worktree.
- **A personal wiki.** Give the wiki a private git remote, then clone it on
  the next machine with `lore setup --wiki-clone <url>`. `lore config`
  marks a wiki without git with `!`.

## If something's wrong

See [troubleshooting.md](troubleshooting.md) — start with `lore status`,
then re-run `lore setup`, which repairs what it can and names each repair.

## Done when

`lore setup` prints only check marks, `lore status` shows the routed wiki
with no alerts, and — after you've used Claude Code for a bit in the repo —
`status`'s `Hook` line carries a recent timestamp. Capture itself writes
only a transcript-ledger entry; nothing lands in the wiki until you write a
note, run `/lore:inbox`, or merge a pull request an agent opened on the wiki
repo.
