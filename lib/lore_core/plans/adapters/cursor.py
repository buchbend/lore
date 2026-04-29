"""Cursor IDE plan-mode payload adapter (stub).

Cursor's plan-mode ships markdown in a producer-specific envelope
shape that we don't yet have a concrete sample for. This stub
documents where the adapter goes; ship the real implementation when
we have a payload sample.

To activate, wire it into :data:`adapters.ADAPTERS` once the
implementation is real.
"""
from __future__ import annotations


def detect(payload: dict) -> bool:
    """Stub — never matches until the real Cursor shape is encoded."""
    return False


def extract(payload: dict) -> tuple[str | None, str]:
    """Stub — never produces output."""
    return None, "cursor-stub"
