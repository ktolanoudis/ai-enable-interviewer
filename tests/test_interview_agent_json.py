import sys
import unittest
from pathlib import Path

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import interview_agent  # noqa: E402


class InterviewAgentJsonTests(unittest.TestCase):
    def test_extract_json_loose_ignores_trailing_text(self):
        parsed = interview_agent._extract_json_loose('{"tasks": [], "ready_for_report": false}\nextra')

        self.assertEqual(parsed["tasks"], [])
        self.assertFalse(parsed["ready_for_report"])

    def test_extract_json_loose_uses_first_valid_object_when_multiple_objects_returned(self):
        parsed = interview_agent._extract_json_loose('{"tasks": [{"name": "Search experts"}]}\n{"ignored": true}')

        self.assertEqual(parsed["tasks"][0]["name"], "Search experts")
        self.assertNotIn("ignored", parsed)

    def test_extract_json_loose_handles_json_fence(self):
        parsed = interview_agent._extract_json_loose('```json\n{"questions": ["What tools do you use?"]}\n```')

        self.assertEqual(parsed["questions"], ["What tools do you use?"])


if __name__ == "__main__":
    unittest.main()
