"""Curator B must skip surfaces marked ``authored_by: curator_a``.

The session surface is Curator A's territory — it owns date-sharded path
layout (``sessions/<YYYY>/<MM>/<DD>-<slug>.md``) via session_writer.
When Curator B is allowed to extract into ``session`` it bypasses that
layout and dumps notes flat under ``sessions/`` (see issue logged
2026-04-28). The fix is declarative: SURFACES.md tags ``session`` with
``authored_by: curator_a`` and Curator B filters it out.
"""
from __future__ import annotations

from pathlib import Path

from lore_core.surfaces import SurfaceDef, SurfacesDoc, extractable_surfaces


def _doc(surfaces: list[SurfaceDef]) -> SurfacesDoc:
    return SurfacesDoc(schema_version=2, surfaces=surfaces, path=Path("<test>"))


def test_extractable_surfaces_excludes_curator_a_authored() -> None:
    doc = _doc([
        SurfaceDef(name="concept", description="", required=[], optional=[]),
        SurfaceDef(
            name="session", description="", required=[], optional=[],
            authored_by="curator_a",
        ),
        SurfaceDef(name="decision", description="", required=[], optional=[]),
    ])
    names = [s.name for s in extractable_surfaces(doc)]
    assert names == ["concept", "decision"]


def test_extractable_surfaces_includes_unmarked_surfaces() -> None:
    doc = _doc([
        SurfaceDef(name="concept", description="", required=[], optional=[]),
        SurfaceDef(name="decision", description="", required=[], optional=[]),
    ])
    names = [s.name for s in extractable_surfaces(doc)]
    assert names == ["concept", "decision"]


def test_extractable_surfaces_keeps_other_authored_by_values() -> None:
    """Forward-compat: only ``curator_a`` is filtered. Unknown values pass
    through so future authors (e.g. ``curator_c``, external tools) don't
    silently disappear from clustering."""
    doc = _doc([
        SurfaceDef(
            name="thread", description="", required=[], optional=[],
            authored_by="derived",
        ),
        SurfaceDef(name="concept", description="", required=[], optional=[]),
    ])
    names = [s.name for s in extractable_surfaces(doc)]
    assert names == ["thread", "concept"]


def test_extractable_surfaces_skips_unmarked_session_surface() -> None:
    """Backward compat: a legacy SURFACES.md without the ``authored_by``
    flag must still treat ``session`` as Curator A's territory.
    Existing vaults must not regress while users migrate."""
    doc = _doc([
        SurfaceDef(name="concept", description="", required=[], optional=[]),
        SurfaceDef(name="session", description="", required=[], optional=[]),
        SurfaceDef(name="decision", description="", required=[], optional=[]),
    ])
    names = [s.name for s in extractable_surfaces(doc)]
    assert names == ["concept", "decision"]
