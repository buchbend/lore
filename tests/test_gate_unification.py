"""One canonical GateResult; PublishGate satisfies the Gate seam.

The generative layers and the publish gate each grew their own ``GateResult``
with identical fields. The flush integration needs a single verdict type
flowing extractor→gate→append, and it needs the real
:class:`~lore_core.publish_gate.PublishGate` to slot into the ``Gate`` protocol
the extractor consumes. These tests pin both.
"""

from __future__ import annotations

import secrets as _secrets

from lore_core import publish_gate as pg
from lore_curator import fact_extract as fe
from lore_curator.chunker import Chunk


def test_canonical_gate_result_has_ok_and_withheld_constructors():
    ok = pg.GateResult.ok()
    assert ok.passed is True
    assert ok.category == "" and ok.feedback == ""
    withheld = pg.GateResult.withheld("secret", "contains an API key")
    assert withheld.passed is False
    assert withheld.category == "secret"
    assert withheld.feedback == "contains an API key"


def test_publish_gate_satisfies_the_gate_protocol():
    gate = pg.PublishGate()
    assert isinstance(gate, pg.Gate)


def test_publish_gate_passes_clean_chapter():
    gate = pg.PublishGate()
    result = gate.evaluate("**Traced the flush race**\n\nThe buffer accumulated turns. @42")
    assert result.passed is True


def test_publish_gate_withholds_planted_secret():
    gate = pg.PublishGate()
    token = _secrets.token_urlsafe(40)
    result = gate.evaluate(f"**Notes**\n\nkey sk-{token} leaked. @1")
    assert result.passed is False
    assert result.category == pg.CATEGORY_SECRET


def test_publish_gate_forwards_its_detector():
    class Hit:
        def detect(self, text):
            return "pii"

    gate = pg.PublishGate(detector=Hit())
    result = gate.evaluate("**Reviewed the merge**\n\nordinary prose. @1")
    assert result.passed is False
    assert result.category == pg.CATEGORY_PII


def test_publish_gate_drives_the_extractor_retry():
    # A real PublishGate injected into extract_chunk: a planted secret in the
    # first attempt withholds; the extractor retries.
    from typing import Any

    from lore_core.types import Turn

    class _FakeBlock:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.type = "tool_use"
            self.input = payload

    class _FakeResponse:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.content = [_FakeBlock(payload)]
            self.model = "m"

    class _Messages:
        def __init__(self, payloads):
            self._payloads = list(payloads)
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _FakeResponse(self._payloads.pop(0))

    class _Client:
        def __init__(self, msgs):
            self.messages = msgs

    token = _secrets.token_urlsafe(40)
    dirty = {"facts": [{"kind": "done", "text": f"Leaked sk-{token}.", "anchor": 2}]}
    clean = {"facts": [{"kind": "done", "text": "Discussed the leak.", "anchor": 2}]}
    client = _Client(_Messages([dirty, clean]))
    turns = [Turn(index=2, timestamp=None, role="user", text="x")]

    result = fe.extract_chunk(
        chunk=Chunk(from_turn=2, to_turn=2),
        turns=turns,
        llm_client=client,
        model="m",
        gate=pg.PublishGate(),
    )
    assert result.status is fe.ExtractStatus.EXTRACTED
    assert result.attempts == 2
    assert token not in " ".join(f.text for f in result.facts)
