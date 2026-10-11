"""Tests for `lore_core.session.slugify`."""

from __future__ import annotations

from lore_core.session import slugify


def test_slugify_basic():
    assert slugify("Fix Retry Logic") == "fix-retry-logic"
    assert slugify("multi  word--with__chars!?") == "multi-word-with-chars"
    assert slugify("a" * 200).startswith("aaa")
    assert len(slugify("a" * 200)) <= 60
