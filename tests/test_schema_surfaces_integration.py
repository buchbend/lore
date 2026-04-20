"""Tests for schema.required_fields_for() — integration with per-wiki SURFACES.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_core.schema import REQUIRED_FIELDS, required_fields_for


class TestRequiredFieldsFallback:
    """When wiki_dir is absent or SURFACES.md doesn't exist."""

    def test_required_fields_falls_back_when_no_wiki_dir(self):
        """required_fields_for("concept") returns REQUIRED_FIELDS["concept"] list."""
        result = required_fields_for("concept")
        assert result == REQUIRED_FIELDS["concept"]

    def test_required_fields_falls_back_when_surfaces_md_missing(self, tmp_path: Path):
        """Pass wiki_dir=tmp_path (no SURFACES.md), still returns REQUIRED_FIELDS."""
        result = required_fields_for("concept", wiki_dir=tmp_path)
        assert result == REQUIRED_FIELDS["concept"]


class TestRequiredFieldsFromSurfaces:
    """When SURFACES.md exists and declares the type."""

    def test_required_fields_uses_surfaces_md_when_present(self, tmp_path: Path):
        """Write SURFACES.md declaring concept with custom required fields."""
        surfaces_md = tmp_path / "SURFACES.md"
        surfaces_md.write_text("""schema_version: 2

## concept

Custom concept surface.

```yaml
required: [type, my_custom_field]
optional: [description, tags]
```
""")
        result = required_fields_for("concept", wiki_dir=tmp_path)
        # Should use the SURFACES.md definition, not the legacy REQUIRED_FIELDS
        assert result == ["type", "my_custom_field"]


class TestRequiredFieldsErrors:
    """Error cases."""

    def test_required_fields_raises_keyerror_for_unknown_type(self):
        """Raise KeyError when type is not in REQUIRED_FIELDS or SURFACES.md."""
        with pytest.raises(KeyError, match="nonexistent"):
            required_fields_for("nonexistent")

    def test_required_fields_raises_keyerror_when_type_missing_from_surfaces(
        self, tmp_path: Path
    ):
        """Raise KeyError if SURFACES.md exists but doesn't declare the type."""
        surfaces_md = tmp_path / "SURFACES.md"
        surfaces_md.write_text("""schema_version: 2

## concept

Concept definition.

```yaml
required: [type]
```
""")
        # Try to get a type that's not in SURFACES.md and not in REQUIRED_FIELDS
        with pytest.raises(KeyError, match="nonexistent"):
            required_fields_for("nonexistent", wiki_dir=tmp_path)


class TestBackwardCompat:
    """Ensure REQUIRED_FIELDS dict is still exported and unchanged."""

    def test_existing_required_fields_dict_unchanged_for_legacy_callers(self):
        """REQUIRED_FIELDS still exists with same keys and lists."""
        assert "project" in REQUIRED_FIELDS
        assert "concept" in REQUIRED_FIELDS
        assert "decision" in REQUIRED_FIELDS
        assert "session" in REQUIRED_FIELDS
        assert "paper" in REQUIRED_FIELDS
        # Spot-check the content hasn't changed
        assert "type" in REQUIRED_FIELDS["concept"]
        assert "created" in REQUIRED_FIELDS["concept"]


class TestReturnsCopy:
    """Ensure returned lists are copies, not the internal lists."""

    def test_required_fields_returns_a_copy_not_the_internal_list(self):
        """Mutating the returned list does NOT affect subsequent calls."""
        result1 = required_fields_for("concept")
        original_len = len(result1)
        result1.append("mutated_field")
        assert len(result1) == original_len + 1

        # Second call should return the original list, not the mutated one
        result2 = required_fields_for("concept")
        assert len(result2) == original_len
        assert "mutated_field" not in result2
