"""The publish gate evaluates flag text, and nothing else.

The gate used to sit in front of two writers: the chapter composer and the
flag writer. The composer retired, so the injectable-``Gate``-object
machinery it needed — a Protocol, a pass-through stand-in, and a class
wrapper around one pure function — has no second implementation left to
abstract over. The flag writer calls :func:`evaluate` directly.

Covers the teardown's third acceptance criterion: the publish gate evaluates
flag text only.
"""

from __future__ import annotations

import pytest


def test_evaluate_names_its_input_for_the_only_caller_left() -> None:
    """`chapter_text` would be a lie: a flag is not a chapter."""
    import inspect

    from lore_core.publish_gate import evaluate

    params = list(inspect.signature(evaluate).parameters)
    assert "chapter_text" not in params, (
        f"evaluate still names its input after the retired composer: {params}"
    )
    assert params[0] == "text", f"expected a neutral first parameter, got {params}"


@pytest.mark.parametrize("name", ["Gate", "PassThroughGate", "PublishGate"])
def test_the_injectable_gate_machinery_is_gone(name: str) -> None:
    """One caller calling one function needs no Protocol and no wrapper."""
    import lore_core.publish_gate as pg

    assert not hasattr(pg, name), (
        f"{name} existed so the chapter composer could inject a gate; "
        f"the composer is gone and flag.py calls evaluate() directly"
    )


def test_the_gate_still_withholds_a_flag_carrying_a_secret() -> None:
    """The behaviour the flag writer depends on is unchanged."""
    from lore_core.publish_gate import evaluate

    verdict = evaluate("token AKIAIOSFODNN7EXAMPLE leaked into the config")
    assert not verdict.passed
    assert verdict.category


def test_the_gate_passes_ordinary_flag_text() -> None:
    from lore_core.publish_gate import evaluate

    verdict = evaluate("The reaper starves mid-drain when the queue is empty.")
    assert verdict.passed
