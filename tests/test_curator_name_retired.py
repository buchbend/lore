"""Issue #597: no live code path carries the name "curator".

The only allowed matches are the recognisers that reject a retired
``curator:`` config block, so an old config still loads with a warning.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# path relative to REPO -> the one line allowed to say "curator" there
ALLOWED = {
    "lib/lore_core/root_config.py": 'RETIRED_BLOCKS = frozenset({"curator", "journal"})',
    "lib/lore_core/wiki_config.py": (
        'RETIRED_BLOCKS = frozenset({"curator", "briefing", "models", "heartbeat"})'
    ),
}


def test_no_lore_curator_package() -> None:
    assert not (REPO / "lib" / "lore_curator").exists()


def test_capture_modules_live_in_core() -> None:
    from lore_core import capture_routing, ledger_linkage

    assert callable(capture_routing.route_capture)
    assert callable(ledger_linkage.build_linkage)


def test_no_curator_word_in_lib_or_skills() -> None:
    hits: list[str] = []
    for root in ("lib", "skills"):
        for path in sorted((REPO / root).rglob("*")):
            if not path.is_file() or "egg-info" in str(path) or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text()
            except UnicodeDecodeError:
                continue
            rel = path.relative_to(REPO).as_posix()
            for n, line in enumerate(text.splitlines(), 1):
                if "curator" in line.lower() and line.strip() != ALLOWED.get(rel):
                    hits.append(f"{rel}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)
