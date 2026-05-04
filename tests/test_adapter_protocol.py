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

    def transcript_path_for_id(self, session_id: str, cwd: Path) -> Path | None:
        return None


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

    def transcript_path_for_id(self, session_id: str, cwd: Path) -> Path | None:
        return None


class _MissingIntegrationAttr:
    """Stub missing the `integration` class attribute."""

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

    def transcript_path_for_id(self, session_id: str, cwd: Path) -> Path | None:
        return None


class _MissingTranscriptPathForId:
    """Stub missing the new `transcript_path_for_id` method."""

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


def test_protocol_has_runtime_check():
    """A class with all five methods + `integration` attribute passes isinstance(instance, Adapter)."""
    stub = _FullStub()
    assert isinstance(stub, Adapter)


def test_protocol_rejects_missing_method():
    """A class missing `read_slice_after_hash` fails isinstance(instance, Adapter)."""
    stub = _MissingReadSliceAfterHash()
    assert not isinstance(stub, Adapter)


def test_protocol_rejects_missing_integration_attr():
    """A class missing the `integration` class attribute fails isinstance(instance, Adapter)."""
    stub = _MissingIntegrationAttr()
    assert not isinstance(stub, Adapter)


def test_protocol_rejects_missing_transcript_path_for_id():
    """A class missing `transcript_path_for_id` fails isinstance(instance, Adapter).

    The method was added so Phase 2 synthesis can re-build a
    TranscriptHandle from cwd + session id. Adapters that lack it
    silently break the conversation-slice prompt path.
    """
    stub = _MissingTranscriptPathForId()
    assert not isinstance(stub, Adapter)
