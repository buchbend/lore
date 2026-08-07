"""lore_curator — the capture-side and hygiene helpers.

The package defines no entry point of its own; each module is imported
directly by its caller. It holds:

* :mod:`lore_curator.llm_client` — a backend-agnostic LLM client (Claude
  subscription, API key, or a local endpoint), used by the briefing;
* :mod:`lore_curator.capture_routing` — the decision layer between a hook
  firing and the transcript ledger;
* :mod:`lore_curator.hygiene` — the frontmatter-only passes behind the
  ``lore curator`` command;
* :mod:`lore_curator.ledger_linkage` — builds a ledger entry's linkage
  block from git state and transcript turns.

Nothing here composes a note. The package name is historical: renaming it
would touch every importer and change no behaviour.
"""
