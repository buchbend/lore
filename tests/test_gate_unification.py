"""One canonical GateResult; PublishGate satisfies the compose Gate seam.

The chapter composer (#125) and the publish gate (#126) each grew their
own ``GateResult`` with identical fields. The flush integration needs a
single verdict type flowing composer→gate→append, and it needs the real
:class:`~lore_core.publish_gate.PublishGate` to slot into the ``Gate``
protocol the composer consumes. These tests pin both.
"""

from __future__ import annotations

import secrets as _secrets

from lore_core import publish_gate as pg
from lore_curator import chapter_compose as cc


def test_gate_result_is_one_canonical_class():
    # The composer re-exports the gate's GateResult — not a second copy.
    assert cc.GateResult is pg.GateResult


def test_canonical_gate_result_has_ok_and_withheld_constructors():
    ok = pg.GateResult.ok()
    assert ok.passed is True
    assert ok.category == "" and ok.feedback == ""
    withheld = pg.GateResult.withheld("secret", "contains an API key")
    assert withheld.passed is False
    assert withheld.category == "secret"
    assert withheld.feedback == "contains an API key"


def test_publish_gate_satisfies_the_compose_gate_protocol():
    gate = pg.PublishGate()
    assert isinstance(gate, cc.Gate)


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


def test_publish_gate_drives_the_composer_retry():
    # A real PublishGate injected into compose_chapter: a planted secret
    # in the first attempt withholds; the composer retries.
    from typing import Any

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
    dirty = {"blocks": [{"lead": "Leaked a key", "body": f"sk-{token}", "anchor": 2}]}
    clean = {"blocks": [{"lead": "Discussed the leak", "body": "no value", "anchor": 2}]}
    msgs = _Messages([dirty, clean])
    client = _Client(msgs)

    result = cc.compose_chapter(
        slice_text="[user@2] x",
        slice_from_turn=2,
        slice_to_turn=2,
        note_so_far="note",
        llm_client=client,
        model="m",
        gate=pg.PublishGate(),
    )
    assert result.status is cc.ComposeStatus.COMPOSED
    assert result.attempts == 2
    assert token not in cc.render_chapter_body(result.chapter)
