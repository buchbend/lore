"""A wiki config carrying a retired compose key still loads.

The per-wiki `curator.*` block tuned the compose pipeline: the spawn
threshold, the noteworthy tier, the synthesis buffer caps, the reaper budget.
All of it retired with the pipeline. A config file written before the
teardown must not become a hard error — lore warns and carries on with the
rest of the file intact.

`curator.backend` is a different setting living in the root config, and is
untouched by this.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

RETIRED_KEYS = [
    "threshold_pending_turns",
    "max_pending_age_s",
    "a_noteworthy_tier",
    "curator_a_cooldown_s",
    "synthesis_buffer_cap_turns",
    "synthesis_buffer_cap_chars",
    "synthesis_flush_timeout_s",
    "synthesis_model_tier",
    "reaper_max_per_pass",
    "buffer_done_retention_days",
    "liveness_stale_threshold_s",
]


def _write(wiki_dir: Path, body: str) -> None:
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / ".lore-wiki.yml").write_text(body, encoding="utf-8")


def test_the_wiki_config_no_longer_declares_a_curator_block() -> None:
    from lore_core.wiki_config import WikiConfig

    assert not hasattr(WikiConfig(), "curator"), (
        "the per-wiki curator block tuned the compose pipeline and must retire with it"
    )


@pytest.mark.parametrize("key", RETIRED_KEYS)
def test_a_retired_key_warns_and_keeps_loading(tmp_path: Path, key: str) -> None:
    from lore_core.wiki_config import load_wiki_config

    _write(tmp_path, f"curator:\n  {key}: 7\ngit:\n  auto_push: true\n")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_wiki_config(tmp_path)

    assert any(key in str(w.message) or "curator" in str(w.message) for w in caught), (
        f"loading a config with retired key {key!r} warned nothing: "
        f"{[str(w.message) for w in caught]}"
    )
    assert cfg.git.auto_push is True, "the rest of the file must still apply"


def test_a_whole_retired_block_warns_once(tmp_path: Path) -> None:
    """One warning for the block, not one per key inside it."""
    from lore_core.wiki_config import load_wiki_config

    body = "curator:\n" + "".join(f"  {k}: 1\n" for k in RETIRED_KEYS)
    _write(tmp_path, body + "git:\n  auto_push: true\n")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        cfg = load_wiki_config(tmp_path)

    curator_warnings = [w for w in caught if "curator" in str(w.message)]
    assert len(curator_warnings) == 1, (
        f"expected one warning for the retired block, got {len(curator_warnings)}: "
        f"{[str(w.message) for w in curator_warnings]}"
    )
    assert cfg.git.auto_push is True


def test_curator_backend_in_the_root_config_is_untouched() -> None:
    """The backend selector is a root-config setting and survives the teardown."""
    from lore_core.root_config import RootConfig

    assert hasattr(RootConfig(), "curator"), (
        "root config keeps `curator.backend` — only the per-wiki compose knobs retire"
    )
