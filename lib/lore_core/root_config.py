"""Root-level Lore config at $LORE_ROOT/.lore/config.yml.

Observability settings are global (not per-wiki) because the log
streams they govern live at $LORE_ROOT/.lore/ and are shared across
wikis.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class HookEventsConfig:
    max_size_mb: int = 10
    keep_rotations: int = 1


@dataclass
class RunsConfig:
    keep: int = 200
    max_total_mb: int = 100
    keep_trace: int = 30


@dataclass
class ProcConfig:
    keep_generations: int = 3


@dataclass
class ObservabilityConfig:
    hook_events: HookEventsConfig = field(default_factory=HookEventsConfig)
    runs: RunsConfig = field(default_factory=RunsConfig)
    proc: ProcConfig = field(default_factory=ProcConfig)


@dataclass
class OpenAIBackendConfig:
    """Settings for an OpenAI-compatible curator backend (e.g. local model gateways).

    ``base_url`` is the OpenAI-compatible API root (e.g. ``https://chat.kiconnect.nrw/api/v1``).
    ``api_key_env`` names the env var holding the API key — this stays out of
    config files. The recommended persistent home for the key itself is
    ``$LORE_ROOT/.lore/secrets.env`` (see :mod:`lore_core.secrets_env`); that
    file is auto-loaded into ``os.environ`` at curator startup, lives inside
    the gitignored ``.lore/`` directory, and never appears in diffs.
    ``model_{simple,middle,high}`` override the Anthropic tier names; leave empty to fall
    back to the env var ``LORE_OPENAI_MODEL_{SIMPLE,MIDDLE,HIGH}`` or pass-through.
    """

    base_url: str = ""
    api_key_env: str = "LORE_OPENAI_API_KEY"
    model_simple: str = ""
    model_middle: str = ""
    model_high: str = ""


@dataclass
class CuratorBackendConfig:
    """Curator LLM backend selection + cascade mode.

    ``backend`` is one of: ``"auto"`` | ``"subscription"`` | ``"api"`` | ``"openai"``.
    ``auto`` prefers claude-on-PATH → ANTHROPIC_API_KEY → OpenAI (if configured) → None.
    Env var ``LORE_LLM_BACKEND`` and CLI ``--backend`` override this config value.

    ``noteworthy_mode`` controls the feature-based cascade in Curator A:
    ``"cascade"`` (default) skips the LLM on clearly-trivial slices and
    only calls it for uncertain/substantive ones; ``"llm_only"`` always
    calls the LLM (cascade still runs but for telemetry). Env var
    ``LORE_NOTEWORTHY_MODE`` overrides this config value.
    """

    backend: str = "auto"
    openai: OpenAIBackendConfig = field(default_factory=OpenAIBackendConfig)
    noteworthy_mode: str = "cascade"

    # Buffer-and-flush curator feature flag. PR 3 of the
    # very-good-thats-the-mossy-lobster plan flipped this from False to
    # True; the heartbeat path now buffers deterministic chunk deltas
    # and synthesises once at flush time (SessionEnd / cap-trip /
    # reaper) instead of LLM-classifying every chunk.
    #
    # Escape hatch: set ``LORE_BUFFER_FLUSH=0`` in the environment, or
    # ``curator.use_buffer_flush: false`` in ``$LORE_ROOT/.lore/config.yml``,
    # to fall back to the legacy classify-per-chunk path. The legacy
    # path stays compiled in until PR 4 (next minor release) deletes
    # it.
    use_buffer_flush: bool = True

    # LLM-gated commit attribution at the Stop hook
    # (``_attribute_commits_with_judgment`` in ``lore_cli/hooks.py``).
    # When True (default) Lore asks the configured backend to judge,
    # for each recent commit without a ``Plan:`` trailer that touches
    # an active plan's ``step_files``, whether the commit completes
    # the step. Bounded per-session by a seen-set, but it does issue
    # one LLM call per matching ``(commit, plan-step)`` triple.
    #
    # Set ``curator.closure_judgment_enabled: false`` in
    # ``$LORE_ROOT/.lore/config.yml`` to skip the path entirely and
    # fall back to trailer-only nudges (commits with ``Plan:`` trailers
    # still auto-advance their steps; only LLM-judged attribution for
    # un-trailered commits is parked). Env var ``LORE_DISABLE_LLM_JUDGMENT=1``
    # is an ops override that wins over config.
    closure_judgment_enabled: bool = True


@dataclass
class JournalConfig:
    """AI + human freeform journal feature flag.

    Off by default. Enabling injects a SessionStart prompt fragment
    inviting the model to append to ``journals/ai.md`` when it has
    something noteworthy, and exposes ``lore_journal_{read,write}``
    MCP tools. Distinct from the per-day journal *surface* (auto-
    extracted by Curator B) — see ``project_lore_journal_idea``.
    """

    enabled: bool = False


@dataclass
class RootConfig:
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    curator: CuratorBackendConfig = field(default_factory=CuratorBackendConfig)
    journal: JournalConfig = field(default_factory=JournalConfig)


def _merge(target: Any, raw: dict[str, Any], path: str, source: Path) -> None:
    """Merge raw into target dataclass in place; warn on unknown keys."""
    valid = {f.name for f in fields(target)}
    for key, value in raw.items():
        if key not in valid:
            qualified = f"{path}.{key}" if path else key
            warnings.warn(f"root_config: unknown key {qualified!r} in {source}", stacklevel=3)
            continue
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value, f"{path}.{key}" if path else key, source)
        else:
            setattr(target, key, value)


def load_root_config(lore_root: Path) -> RootConfig:
    """Load $LORE_ROOT/.lore/config.yml over defaults.

    Missing file / missing section / unknown keys → defaults + warning.
    Malformed YAML → defaults + warning (no crash).
    """
    cfg = RootConfig()
    path = lore_root / ".lore" / "config.yml"
    if not path.exists():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        warnings.warn(f"root_config: malformed YAML at {path}: {e}", stacklevel=2)
        return cfg
    if not isinstance(raw, dict):
        warnings.warn(f"root_config: top-level must be a mapping at {path}", stacklevel=2)
        return cfg
    _merge(cfg, raw, "", path)
    return cfg
