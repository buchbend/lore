# Onboarding: install → wizard → first attach → verify

**Goal:** go from a fresh machine to "notes are being written" in one
guided pass, and know how to tell it worked.

`lore init` is the single onboarding wizard (PRD 0005, pillar C) — it
replaced the old five-loosely-chained-steps flow (installer → integration
wiring → vault init → wiki creation → repo attach) with one idempotent,
resumable command. Re-running it is safe: each step detects already-done
state and collapses to a `✓` skip line, so `lore init` doubles as the
repair path for a partial install.

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
   runnable on `PATH` afterward — don't treat a silent-looking finish as
   success without checking the exit code.

2. **Run the wizard.**

   ```
   lore init
   ```

   Six steps, each collapsing to a `✓` receipt line as it completes:

   | Step | What it does |
   |---|---|
   | 1 · Vault | Picks the vault location (`$LORE_ROOT`, or `~/lore` by default). |
   | 2 · Wiki | New personal wiki, clone a team remote, or link an existing directory — scaffolds `_scopes.yml` with a commented example. |
   | 3 · Integrations | Detects Claude Code / Cursor and wires them in (reuses `lore install`'s plumbing); a plugin-cache refresh failure is loud, not swallowed. |
   | 4 · First attach | If `cwd` is a git repo, runs the attach wizard inline (skip with no flag if you'd rather attach later). |
   | 5 · Doctor | Runs `lore doctor` automatically; the wizard's own exit code mirrors its verdict. |
   | 6 · Handoff | Prints the summary panel, doctor results, and next steps — including "restart Claude Code", which is required for hook/skill changes to take effect. |

   Every step has a non-interactive flag, so the whole wizard scripts:

   ```
   lore init --wiki-new personal --attach --yes
   lore init --wiki-clone git@github.com:you/team-wiki.git --yes
   lore init --vault ~/lore --wiki-link ~/existing-wiki --yes
   ```

   `--plain` degrades every prompt to plain stdin (no Rich panels) for
   terminals that don't render them well; `--force` overwrites an
   existing `CLAUDE.md`/`templates/` if you're deliberately re-scaffolding.

3. **Restart Claude Code.** Hooks and skills are picked up at session
   start — the handoff step (6) tells you this explicitly; don't skip it.

4. **Verify.** From the repo you attached (or any repo, to check the
   install broadly):

   ```
   lore doctor
   lore status
   ```

   `doctor` re-confirms install integrity (nonzero exit on any failure);
   `status` shows whether a session has actually captured anything yet
   (capture liveness, last hook fire, last note). A fresh install with no
   session activity yet is expected to show "no activity" lines, not
   errors — errors there mean something in step 2 needs a re-run.

## If something's wrong

See [troubleshooting.md](troubleshooting.md) — start with `lore status`,
escalate to `lore doctor --fix` for repairable state, then `lore trace`
for a specific flush's story.

## Done when

`lore doctor` exits 0, `lore status` shows the attached wiki with no
alerts, and — after you've actually used Claude Code for a bit in the
attached repo — a session note appears under
`<wiki>/sessions/[<handle>/]YYYY/MM/`.
