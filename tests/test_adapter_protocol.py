"""Tests for `lore_adapters.protocol` — Adapter protocol definition."""

from __future__ import annotations

from pathlib import Path

from lore_adapters.protocol import Adapter
from lore_core.types import TranscriptHandle


class _FullStub:
    """Minimal stub implementing all Adapter requirements."""

    integration = "test"

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        return []

    def read_slice(
        self,
        handle: TranscriptHandle,
        from_index: int = 0,
    ):
        yield from ()

    def read_slice_after_hash(
        self,
        handle: TranscriptHandle,
        after_hash: str | None,
        index_hint: int | None = None,
    ):
        yield from ()

    def is_complete(self, handle: TranscriptHandle) -> bool:
        return True


class _MissingReadSliceAfterHash:
    """Stub missing the `read_slice_after_hash` method."""

    integration = "test"

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        return []

    def read_slice(
        self,
        handle: TranscriptHandle,
        from_index: int = 0,
    ):
        yield from ()

    def is_complete(self, handle: TranscriptHandle) -> bool:
        return True


class _MissingHostAttr:
    """Stub missing the `host` class attribute."""

    def list_transcripts(self, directory: Path) -> list[TranscriptHandle]:
        return []

    def read_slice(
        self,
        handle: TranscriptHandle,
        from_index: int = 0,
    ):
        yield from ()

    def read_slice_after_hash(
        self,
        handle: TranscriptHandle,
        after_hash: str | None,
        index_hint: int | None = None,
    ):
        yield from ()

    def is_complete(self, handle: TranscriptHandle) -> bool:
        return True


def test_protocol_has_runtime_check():
    """A class with all four methods + `host` attribute passes isinstance(instance, Adapter)."""
    stub = _FullStub()
    assert isinstance(stub, Adapter)


def test_protocol_rejects_missing_method():
    """A class missing `read_slice_after_hash` fails isinstance(instance, Adapter)."""
    stub = _MissingReadSliceAfterHash()
    assert not isinstance(stub, Adapter)


def test_protocol_rejects_missing_host_attr():
    """A class missing the `host` class attribute fails isinstance(instance, Adapter)."""
    stub = _MissingHostAttr()
    assert not isinstance(stub, Adapter)
