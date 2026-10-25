---
name: lore:system
description: Bring every local checkout of a system to its target branch, using
  the system map. Shows `lore system status`, walks the repos producers first,
  offers to settle local work (commit, push, finish or abort a merge) and acts
  on each only after the person says yes, then switches and fast-forwards. Never
  discards work. Run with "/lore:system [branch] [system]".
user_invocable: true
---

# Bring a system to its target

A system map (`<wiki>/systems/<name>.md`, PRD 0019) lists the repos of one system and the
edges between them. It names repos as `owner/repo`, never folders. Lore finds each checkout
on this machine through a `checkouts` entry in `~/.config/lore/config.yml`, or below a scope
folder by its git remote. The **target** is the branch the system belongs on. It is saved on
this machine only. `lore system status` shows how far each checkout is from it.

The arguments are a branch and a system, the map's note name. Without a branch, use the
saved target. If neither exists, show the status and ask for a branch. Without a system, use
the only map, or ask which one.

## Rules

- **Ask before every change, one repo at a time.** A commit, a push, an abort, a `.gitignore`
  line or a config line runs only after the person says yes for that repo. A yes for one
  repo covers no other repo.
- **Never discard work.** Never run `reset`, `clean`, `checkout -- <file>`, `restore`,
  `stash drop`, `branch -D` or a force push, even when asked in passing. Where the person
  wants to throw work away, show the exact command and let them run it.
- **Commit with care.** Before a commit, show `git -C <path> status --short` and
  `git -C <path> diff --stat`, then propose a message in the repo's own style
  (`git -C <path> log --oneline -5`). Stage the files the person names, or all after their
  yes. Never pass `--no-verify`. Never amend a commit already pushed.
- **Push plainly.** Push only the current branch to its upstream, with no force flag.
- **Never move a pin.** Name pins that lag the target; editing one is a change of its own.

## Steps

1. **Status.** Run `lore system status <system> --fetch --json`, adding `--branch <branch>`
   when a branch was given. Show the person the text form, `lore system status <system>`,
   so they see the whole system before anything changes. Name each entry of `fetch_failed`
   and `warnings`.
2. **Target.** When a branch was given and it differs from the saved target, propose
   `lore system target <branch> <system>`. Save it after the person says yes.
3. **Local work, producers first.** Walk `checkouts` in order. For each entry of
   `local_work`, show the repo, its path and its work, and offer the fitting options:
   - changed or untracked files: commit them (see the commit rule), add a pattern to
     `.gitignore`, or leave the repo as it is;
   - commits on no remote: push them, or leave the repo;
   - a merge, rebase, cherry-pick or revert in progress, or unmerged files: finish it with
     the person (resolve the conflicts together, then `--continue`), abort it after a yes
     (`git merge --abort`, `git rebase --abort` and so on; say that the abort drops the
     conflict resolution done so far), or leave the repo;
   - a bisect: `git bisect reset` after a yes, or leave the repo.
   A repo the person leaves keeps its branch. Its consumers still switch; name the mismatch
   in the report.
4. **Switch.** Run `lore system status <system> --json` again, with `--branch` if given, so
   the actions match the settled state. In order, for each checkout off its target (`at_target` is false) or behind its upstream
   (`behind` above 0):
   - `switch`: `git -C <path> switch <target>`, then `git -C <path> pull --ff-only`. Where
     only `target_remote` has the branch, `git switch` creates a local branch that tracks it.
   - `pull`: `git -C <path> pull --ff-only`.
   - `default`: the repo has no branch of that name. Switch to `default_branch` and pull with
     `--ff-only`.
   - `blocked`: another worktree, `target_elsewhere`, holds the branch. Leave the checkout
     and name the worktree.
   - `stop`: the repo holds work the person chose to leave. Leave it.
   A `pull --ff-only` that fails means the branch diverged from its upstream. Stop there,
   show both sides (`git -C <path> log --oneline --left-right HEAD...@{upstream}`), and ask
   whether to merge, rebase or leave it. Run the person's choice; never force.
5. **Pins.** Each entry of `pinned` is an edge whose consumer fixes the producer's version.
   A producer on the target does not reach that consumer until its pin moves. Name each one,
   with the file that holds the pin where you find it. Offer the change as a follow-up.
6. **Missing checkouts.** For each entry of `no_checkout`, ask the person where the
   checkout is. If they name a folder, check its remote
   (`git -C <folder> remote get-url origin`) and propose the config line from the entry's
   `hint`. Write it to `~/.config/lore/config.yml` after their yes. Offer to clone it only
   if they ask.
7. **Status again.** Run `lore system status <system>` and show it. That closes the run.

## Report

Show the status before and after. Under it, list what ran in each repo, what the person left
and why, the pins that lag the target, and the repos still without a checkout. The systems
page shows the same: `lore stats --page systems`.
