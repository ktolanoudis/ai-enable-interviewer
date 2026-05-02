import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402


class RecurringThemeMergeTests(unittest.TestCase):
    def test_merge_recurring_themes_uses_semantic_resolution(self):
        existing = [
            {
                "theme_key": "minor_purchase_approval_delays",
                "label": "Minor purchase approval delays",
                "category": "workflow",
                "mention_count": 1,
                "examples": ["Small purchases bounce between manager and finance."],
                "supporting_contributor_keys": ["contrib_1"],
            }
        ]
        incoming = [
            {
                "theme_key": "tooling_signoff_ping_pong",
                "label": "Tooling sign-off ping-pong",
                "category": "workflow",
                "evidence": "Minor tools can bounce between manager, finance, and procurement.",
            }
        ]

        with patch.object(
            db,
            "resolve_recurring_theme",
            return_value={
                "theme_key": "minor_purchase_approval_delays",
                "label": "Minor purchase approval delays",
                "category": "workflow",
                "evidence": "Minor tools can bounce between manager, finance, and procurement.",
            },
        ):
            merged = db._merge_recurring_themes(existing, incoming, contributor_key="contrib_2")

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["theme_key"], "minor_purchase_approval_delays")
        self.assertEqual(merged[0]["mention_count"], 2)
        self.assertIn("contrib_2", merged[0]["supporting_contributor_keys"])
        self.assertIn(
            "Minor tools can bounce between manager, finance, and procurement.",
            merged[0]["examples"],
        )


if __name__ == "__main__":
    unittest.main()
