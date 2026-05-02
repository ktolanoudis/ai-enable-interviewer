import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import feedback_flow  # noqa: E402


class ExistingCapabilityFeedbackTests(unittest.TestCase):
    def test_existing_capability_detector(self):
        self.assertTrue(feedback_flow.is_existing_capability_feedback("I already do this"))
        self.assertTrue(feedback_flow.is_existing_capability_feedback("This already exists in our company"))
        self.assertFalse(feedback_flow.is_existing_capability_feedback("This would help me a lot"))

    def test_existing_capability_rating_does_not_count_as_new_support(self):
        entries = feedback_flow.build_validated_use_case_entries(
            [
                {
                    "use_case_name": "Exercise Drafting",
                    "task_name": "Create exercises",
                    "ai_solution_type": "LLM",
                    "description": "Draft exercises.",
                    "rating": 5,
                    "status": "existing_capability",
                    "comment": "I already do this",
                    "feasibility_feedback": {},
                }
            ],
            {"employee_name": "Anonymous", "role": "Teacher", "department": "Teaching"},
            {"contributor_key": "contrib_1"},
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["existing_capability_count"], 1)
        self.assertEqual(entries[0]["existing_solution_rating_count"], 1)
        self.assertEqual(entries[0]["average_existing_solution_rating"], 5)
        self.assertEqual(entries[0]["rating_count"], 0)
        self.assertEqual(entries[0]["support_count"], 0)

    def test_existing_capability_markdown_uses_current_solution_label(self):
        markdown = feedback_flow.append_use_case_feedback_markdown(
            "# Report\n",
            [
                {
                    "use_case_name": "Exercise Drafting",
                    "rating": 5,
                    "status": "existing_capability",
                    "comment": "I already do this",
                }
            ],
        )

        self.assertIn("Existing capability / already in use", markdown)
        self.assertIn("Current Solution Rating", markdown)


if __name__ == "__main__":
    unittest.main()
