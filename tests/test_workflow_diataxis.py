"""Unit tests for `lore_workflow.diataxis` — the document-epic classification
heuristic.

Ported near-verbatim from ccat-agent-workflow's `tests/test_diataxis.py`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lore_workflow import diataxis

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "merged-epic"


def _load_changeset() -> list[dict]:
    data = json.loads((FIXTURE / "changeset.json").read_text())
    return data["changes"]


class TestQuadrantConstants(unittest.TestCase):
    def test_exactly_the_diataxis_four(self):
        self.assertEqual(
            set(diataxis.QUADRANTS),
            {"tutorial", "how-to", "reference", "explanation"},
        )


class TestExclusion(unittest.TestCase):
    def test_prd_is_excluded(self):
        self.assertTrue(diataxis.is_excluded("docs/prd/0001-widget-export.md"))

    def test_adr_is_excluded(self):
        self.assertTrue(diataxis.is_excluded("docs/adr/0002-export-format.md"))

    def test_nested_prd_adr_excluded(self):
        self.assertTrue(diataxis.is_excluded("project/docs/prd/x.md"))
        self.assertTrue(diataxis.is_excluded("project/docs/adr/y.md"))

    def test_normal_docs_not_excluded(self):
        self.assertFalse(diataxis.is_excluded("docs/how-to/export-a-widget.md"))
        self.assertFalse(diataxis.is_excluded("docs/reference/api.md"))

    def test_excluded_paths_never_get_a_quadrant(self):
        self.assertIsNone(diataxis.classify("docs/prd/0001-widget-export.md"))
        self.assertIsNone(diataxis.classify("docs/adr/0002-export-format.md"))


class TestClassifyDocPaths(unittest.TestCase):
    def test_tutorials_dir(self):
        self.assertEqual(
            diataxis.classify("docs/tutorials/getting-started.md"), "tutorial"
        )

    def test_howto_dir(self):
        self.assertEqual(diataxis.classify("docs/how-to/export-a-widget.md"), "how-to")

    def test_reference_dir(self):
        self.assertEqual(diataxis.classify("docs/reference/api.md"), "reference")

    def test_explanation_dir(self):
        self.assertEqual(
            diataxis.classify("docs/explanation/export-format.md"), "explanation"
        )

    def test_directory_synonyms(self):
        self.assertEqual(diataxis.classify("docs/guide/x.md"), "how-to")
        self.assertEqual(diataxis.classify("docs/guides/x.md"), "how-to")
        self.assertEqual(diataxis.classify("docs/api/x.md"), "reference")
        self.assertEqual(diataxis.classify("docs/concepts/x.md"), "explanation")


class TestClassifySource(unittest.TestCase):
    def test_public_source_routes_to_reference(self):
        self.assertEqual(
            diataxis.classify("src/widgetlib/export.py", public_api=True),
            "reference",
        )

    def test_private_source_is_not_documented(self):
        self.assertIsNone(
            diataxis.classify("src/widgetlib/_internal/serialize.py", public_api=False)
        )

    def test_non_doc_non_source_is_ignored(self):
        self.assertIsNone(diataxis.classify("tests/test_export.py"))
        self.assertIsNone(diataxis.classify("pyproject.toml"))


class TestClassifyChangesetAgainstFixture(unittest.TestCase):
    """End-to-end: classify the fixture changeset and match expected_quadrant."""

    def setUp(self):
        self.changes = _load_changeset()

    def test_every_change_classified_as_expected(self):
        for change in self.changes:
            with self.subTest(path=change["path"]):
                got = diataxis.classify(
                    change["path"],
                    public_api=change.get("public_api", False),
                )
                self.assertEqual(got, change["expected_quadrant"])

    def test_classify_changeset_helper_matches(self):
        results = diataxis.classify_changeset(self.changes)
        by_path = {r["path"]: r for r in results}
        for change in self.changes:
            r = by_path[change["path"]]
            self.assertEqual(r["quadrant"], change["expected_quadrant"])
            if diataxis.is_excluded(change["path"]):
                self.assertIsNone(r["quadrant"])
                self.assertTrue(r["excluded"])

    def test_no_excluded_path_in_edit_plan(self):
        results = diataxis.classify_changeset(self.changes)
        edit_plan = [r for r in results if r["quadrant"] is not None]
        for r in edit_plan:
            self.assertFalse(
                diataxis.is_excluded(r["path"]),
                f"excluded path leaked into edit plan: {r['path']}",
            )
        quadrants_touched = {r["quadrant"] for r in edit_plan}
        self.assertEqual(
            quadrants_touched,
            {"tutorial", "how-to", "reference", "explanation"},
        )
