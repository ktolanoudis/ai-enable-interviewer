import sys
import unittest
from pathlib import Path

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import meta_question_handler  # noqa: E402
import interview_flow  # noqa: E402


class ScopeResolutionTests(unittest.TestCase):
    def test_outside_my_role_is_deterministic(self):
        self.assertEqual(
            meta_question_handler.classify_use_case_scope_resolution("its outside my role")["intent"],
            "outside_role",
        )

    def test_typo_ouside_my_role_is_deterministic(self):
        self.assertEqual(
            meta_question_handler.classify_use_case_scope_resolution("ouside my role")["intent"],
            "outside_role",
        )

    def test_manager_workflow_is_outside_role(self):
        self.assertEqual(
            meta_question_handler.classify_use_case_scope_resolution("that belongs to my manager")["intent"],
            "outside_role",
        )

    def test_scope_feedback_intent_catches_outside_role_typo(self):
        self.assertEqual(
            meta_question_handler.classify_use_case_feedback_response("ouside my role")["intent"],
            "scope_mismatch",
        )

    def test_yes_after_outside_role_skip_prompt_resolves_outside_role(self):
        messages = [
            {
                "role": "assistant",
                "content": "Should I mark it as skipped because it's outside your role?",
            }
        ]

        self.assertEqual(interview_flow._scope_resolution_from_context("yes", messages), "outside_role")


if __name__ == "__main__":
    unittest.main()
