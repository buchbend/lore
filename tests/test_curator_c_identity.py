"""Task 5: Curator C module identity.

Guards against accidental reversion of the module docstring and the
public function name. Pre-Task-5 this module was `lore_curator/core.py`
and the function was `run_curator` — ambiguous with "the curator" in
general. After Plan A the name tells the triad story.
"""

from __future__ import annotations

import inspect


def test_curator_c_module_docstring_mentions_defrag_and_weekly() -> None:
    """Module docstring must identify the role (weekly defrag / converge)."""
    import lore_curator.curator_c as cc

    doc = inspect.getdoc(cc) or ""
    assert "Curator C" in doc, "module docstring must name the role explicitly"
    assert "weekly" in doc.lower(), "module docstring must mention the weekly cadence"
    # Either "defrag" or "converge" is acceptable — both describe what C does.
    assert any(word in doc.lower() for word in ("defrag", "converge", "stale")), (
        "module docstring must describe C's maintenance role"
    )


def test_curator_c_exports_run_curator_c() -> None:
    """Public function renamed to run_curator_c for triad clarity."""
    from lore_curator.curator_c import run_curator_c  # noqa: F401
    from lore_curator import run_curator_c as reexport  # noqa: F401


def test_lore_curator_package_does_not_reexport_old_name() -> None:
    """lore_curator.run_curator (the old ambiguous name) is gone.

    If external code still relies on it, we'd add a back-compat alias with
    a deprecation notice. As of Plan A there are no external callers (the
    only user was the package's own __init__ + its own main), so the rename
    is clean.
    """
    import lore_curator

    assert "run_curator_c" in lore_curator.__all__
    assert "run_curator" not in lore_curator.__all__
