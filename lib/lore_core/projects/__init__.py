"""Project notes — harness-agnostic glue auto-stubbed on ``/lore:attach``.

A project note unifies a repo's CLAUDE.md / AGENTS.md / .cursorrules /
README / pyproject metadata into one Lore-shaped document at
``wiki/<wiki>/projects/<repo-slug>.md``. Every attached repo gets one
regardless of which surface profiles the wiki uses.

Submodules:

* :mod:`harness_parser` — extract sections (description, overview,
  conventions, architecture) from each supported source file. Pure
  text-in / dict-out; no I/O on the wiki side.
* :mod:`stub_generator` — compose the project note from the parsed
  sections + the resolved scope/repo, write it to the wiki, regenerate
  canonical headings on re-stub while preserving user content under
  any other heading.
"""
