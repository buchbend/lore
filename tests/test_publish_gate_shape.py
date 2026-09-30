"""The publish gate takes one argument: the text about to be published.

The gate used to sit in front of the chapter composer, which injected it as
an object. The composer retired, so the injectable-``Gate`` machinery it
needed — a Protocol, a pass-through stand-in, and a class wrapper around one
pure function — has no implementation left to abstract over. Callers call
:func:`evaluate` directly.
"""

from __future__ import annotations

import pytest


def test_evaluate_names_its_input_neutrally() -> None:
    """`chapter_text` would be a lie: the gate scans any outbound text."""
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
        f"the composer is gone and callers call evaluate() directly"
    )


def test_the_gate_still_withholds_text_carrying_a_secret() -> None:
    """The behaviour every outbound writer depends on is unchanged."""
    from lore_core.publish_gate import evaluate

    verdict = evaluate("token AKIAIOSFODNN7EXAMPLE leaked into the config")
    assert not verdict.passed
    assert verdict.category


def test_the_gate_passes_ordinary_text() -> None:
    from lore_core.publish_gate import evaluate

    verdict = evaluate("The reaper starves mid-drain when the queue is empty.")
    assert verdict.passed
