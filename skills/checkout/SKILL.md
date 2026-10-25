---
name: lore:checkout
description: Switch every local checkout of a system to one branch, producers
  first, using the system map. Checks every checkout for local or unmerged work
  first; on any, it warns and switches nothing. Fast-forward only. Reports the
  pinned edges a switch does not reach. Run with "/lore:checkout <branch> [system]".
user_invocable: true
---

# Checkout a system

A system map (`<wiki>/systems/<name>.md`, PRD 0019) lists the repos of one system and the
edges between them. This skill puts each local checkout of the system's repos on one
branch, such as `main` or the feature branch of a change across repos. It reads the map for
the order and for the pins.

The arguments are the branch and, optionally, the system: the map's note name. Without a
system, the skill works on every map. Without a branch, ask for one.

Never discard work. Never stash, reset, rebase, force or delete a branch. Never push. Only
`git fetch`, `git switch` and `git pull --ff-only` run. Never commit, push or clean up the
person's work to get past the check in step 3; that is their call.

## Steps

1. Run `lore map status --system <system> --json` and fetch each checkout it lists:
   `git -C <path> fetch --prune`. A fetch that fails keeps the local refs; name the
   checkout in the report.
2. Run `lore map status --branch <branch> --system <system> --json`. The output holds
   `order`, `checkouts`, `no_checkout`, `pinned` and `local_work`. `checkouts` comes
   producers first; keep that order, so a consumer switches after the repos it reads.
3. Check for local work before anything switches. Each entry of `local_work` names a
   checkout and its `work`: a merge, rebase, cherry-pick, revert or bisect in progress,
   unmerged files, changed files, untracked files, or commits on no remote. Where
   `local_work` is not empty, warn and stop: list each checkout with its path and its work,
   switch no checkout, and say what settles it (finish or abort the operation, commit,
   push, or remove the files). Run the skill again once the person has settled it.
   Proceed only with an empty `local_work`.
4. For each checkout, act on its `action`:
   - `switch`: `git -C <path> switch <branch>`. Where only `target_remote` has the branch,
     `git switch` creates the local branch that tracks it. Then `git -C <path> pull --ff-only`.
   - `pull`: the checkout is on the branch. `git -C <path> pull --ff-only`.
   - `default`: no local or remote branch of that name. Switch to `default_branch` and pull
     with `--ff-only`. Name the repo under "stayed on the default branch". Where
     `default_branch` is empty, leave the checkout as it is and report it.
   - `blocked`: another worktree, `target_elsewhere`, holds the branch. Leave the checkout
     as it is and name the worktree.
   - `stop`: the checkout holds local work. Step 3 has stopped the run already.
   A git command that fails, such as a `pull --ff-only` on a branch that diverged from its
   upstream, stops the run: switch no further checkout, since the consumers after it read
   it. Report what ran and what failed; do not merge or rebase.
5. Read `pinned`. Each entry is an edge whose consumer fixes the producer's version by `sha`,
   `tag`, `image` or `package`. A producer that moved to the branch does not reach that
   consumer until its pin moves. Name each such edge, with the file the pin lives in where
   you find it. Do not edit the pin. Offer it as a follow-up.
6. Name each repo in `no_checkout`: the map lists it, and no scope folder holds a checkout.

## Report

A run that stopped on local work reports only the warning of step 3. Otherwise: one table,
in the order of `checkouts`: repo, branch before, branch after, what ran. Below it, the
blocked checkouts with their reason, the git command that failed (if one did), the repos
that stayed on the default branch, the pinned edges the switch does not reach, and the repos
without a checkout.

The systems page shows the branch of each checkout: `lore stats --page systems`.
