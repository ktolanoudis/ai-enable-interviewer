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

from collection_intent import parse_collection_response  # noqa: E402
from company_flow import metadata_value_from_intent, next_collection_step  # noqa: E402


class CollectionIntentTests(unittest.TestCase):
    def test_email_privacy_refusal_with_at_symbol_is_skip_not_invalid(self):
        parsed = parse_collection_response(
            "email",
            "I’d prefer not to share my personal email, but we typically use a format like first name @ heliogrid.in.",
        )

        self.assertEqual(parsed["intent"], "skip")

    def test_skipped_email_does_not_get_requested_again(self):
        metadata = {
            "email": "",
            "email_opt_out": True,
            "department": "Product",
            "role": "Manager",
        }

        self.assertIsNone(next_collection_step(metadata))

    def test_role_answer_is_normalized_from_sentence(self):
        parsed = parse_collection_response("role", "I am a teacher")

        self.assertEqual(parsed["value"], "Teacher")
        self.assertEqual(metadata_value_from_intent("role", parsed), "Teacher")

    def test_department_answer_does_not_store_role_sentence(self):
        parsed = parse_collection_response("department", "I am a teacher")

        self.assertEqual(parsed["value"], "Teaching")
        self.assertEqual(metadata_value_from_intent("department", parsed), "Teaching")


if __name__ == "__main__":
    unittest.main()
