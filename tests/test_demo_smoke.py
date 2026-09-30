"""Smoke tests for the leadops public demo.

Fast and hermetic: no network, no embedding models, no private data.
Run:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEMO_DB = REPO / "demo" / "crm.demo.sqlite"
LOCAL_DB = REPO / "crm.sqlite"  # gitignored; the UI reads it here


def ensure_demo_db() -> Path:
    """Build the demo DB once (deterministic generator) if it isn't there."""
    if not DEMO_DB.exists():
        result = subprocess.run(
            [sys.executable, "demo/generate_demo.py"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr[-3000:]
    assert DEMO_DB.exists(), "demo DB missing and generation failed"
    return DEMO_DB


class DemoGenerationTests(unittest.TestCase):
    def test_generator_runs_clean(self):
        result = subprocess.run(
            [sys.executable, "demo/generate_demo.py"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        self.assertIn("FTS search documents indexed: 24", result.stdout)

    def test_demo_counts(self):
        ensure_demo_db()
        conn = sqlite3.connect(DEMO_DB)
        try:
            counts = {
                "leads": "SELECT COUNT(*) FROM leadops_leads",
                "profiles": "SELECT COUNT(*) FROM leadops_profiles",
                "search_documents": "SELECT COUNT(*) FROM leadops_search_documents",
                "search_fts": "SELECT COUNT(*) FROM leadops_search_fts",
                "review decisions": "SELECT COUNT(*) FROM leadops_review_decisions",
            }
            for label, sql in counts.items():
                n = conn.execute(sql).fetchone()[0]
                self.assertGreater(n, 0, f"{label} should be non-empty")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM leadops_leads").fetchone()[0], 24
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM leadops_search_fts").fetchone()[0], 24
            )
        finally:
            conn.close()


class FtsSearchTests(unittest.TestCase):
    """The UI's free-text search box ('just type to search') must return hits."""

    @classmethod
    def setUpClass(cls):
        cls.db = ensure_demo_db()

    def _fts(self, query: str) -> list[tuple]:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                """
                SELECT DISTINCT l.lead_id, l.name
                FROM leadops_leads l
                JOIN leadops_search_fts fts ON l.lead_id = fts.lead_id
                WHERE leadops_search_fts MATCH ?
                """,
                (query,),
            ).fetchall()
        finally:
            conn.close()

    def test_search_returns_expected_leads(self):
        self.assertEqual(
            [name for _, name in self._fts("plumbing")],
            ["Piney Woods Plumbing Co."],
        )
        self.assertEqual(
            [name for _, name in self._fts("roofing")],
            ["Sam Houston Roofing LLC"],
        )

    def test_search_matches_profile_body(self):
        # 'seo' only appears in profile markdown bodies, not lead names
        self.assertGreater(len(self._fts("SEO")), 0)

    def test_search_miss_returns_empty(self):
        self.assertEqual(self._fts("zzznosuchtermxyz"), [])


class ListViewTests(unittest.TestCase):
    """The Streamlit list view must render against the demo DB without errors."""

    @classmethod
    def setUpClass(cls):
        ensure_demo_db()
        shutil.copy(DEMO_DB, LOCAL_DB)  # crm.sqlite is gitignored

    def test_app_renders_without_exception(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(REPO / "app.py"))
        at.run()
        self.assertFalse(at.exception, str(at.exception[0].stack_trace) if at.exception else "")


class RetrieveGuardTests(unittest.TestCase):
    """bundle/workflow fail cleanly when private modules are absent."""

    def test_bundle_and_workflow_print_clear_message(self):
        for cmd in (
            ["bundle", "--lead-id", "6201"],
            ["workflow", "similar", "plumbing"],
        ):
            result = subprocess.run(
                [sys.executable, "leadops_retrieve.py", "--db", str(DEMO_DB), *cmd],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("private module not included in the public demo", result.stderr)


if __name__ == "__main__":
    unittest.main()
