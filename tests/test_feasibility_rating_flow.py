import sys
import unittest
from pathlib import Path

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import feedback_flow  # noqa: E402
import interview_flow  # noqa: E402


class FeasibilityRatingFlowTests(unittest.TestCase):
    def test_data_quality_feasibility_actions_are_one_to_five_plus_skip(self):
        actions = feedback_flow.build_use_case_feasibility_rating_actions("data_quality")

        self.assertEqual([a.payload["rating"] for a in actions], ["1", "2", "3", "4", "5", "skip"])
        self.assertTrue(all(a.name == "feasibility_rating" for a in actions))

    def test_regulatory_feasibility_actions_use_risk_labels(self):
        actions = feedback_flow.build_use_case_feasibility_rating_actions("regulatory_risk")

        self.assertEqual([a.payload["rating"] for a in actions], ["low", "medium", "high", "critical", "skip"])

    def test_parse_feasibility_rating_supports_numeric_and_skip(self):
        self.assertEqual(interview_flow._parse_feasibility_rating("data_quality", "4"), 4)
        self.assertEqual(interview_flow._parse_feasibility_rating("explainability", "skip"), "skip")
        self.assertIsNone(interview_flow._parse_feasibility_rating("data_quality", "looks fine"))

    def test_parse_regulatory_rating_supports_labels_and_numeric_fallback(self):
        self.assertEqual(interview_flow._parse_feasibility_rating("regulatory_risk", "medium"), "medium")
        self.assertEqual(interview_flow._parse_feasibility_rating("regulatory_risk", "4"), "critical")


if __name__ == "__main__":
    unittest.main()
