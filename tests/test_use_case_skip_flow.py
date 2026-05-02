import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import interview_flow  # noqa: E402


class _FakeUserSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class UseCaseSkipFlowTests(unittest.TestCase):
    def test_skip_at_opinion_step_skips_rating_and_feasibility(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("awaiting_use_case_opinion", True)
        fake_chainlit.user_session.set("use_case_feedback_index", 0)
        fake_chainlit.user_session.set(
            "pending_report_payload",
            {
                "use_cases": [
                    {
                        "use_case_name": "Skipped use case",
                        "task_name": "Search experts",
                        "ai_solution_type": "drafting",
                        "description": "Draft outreach",
                    },
                    {
                        "use_case_name": "Next use case",
                        "description": "Next description",
                    },
                ]
            },
        )
        fake_chainlit.user_session.set("messages", [])
        sent = []

        async def _fake_send_assistant_message(content, actions=None):
            sent.append({"content": content, "actions": actions or []})

        async def _fake_send_next_use_case_feedback_prompt(send_assistant_message, messages):
            await send_assistant_message("Next use case prompt")
            return "Next use case prompt"

        with patch.object(interview_flow, "cl", fake_chainlit), patch.object(
            interview_flow,
            "classify_use_case_feedback_response",
            return_value={"intent": "uncertain"},
        ), patch.object(
            interview_flow,
            "interpret_use_case_opinion_response",
            return_value={"has_substantive_opinion": False, "opinion_text": "", "included_rating": "skip"},
        ), patch.object(
            interview_flow,
            "send_next_use_case_feedback_prompt",
            side_effect=_fake_send_next_use_case_feedback_prompt,
        ):
            handled = asyncio.run(
                interview_flow.maybe_handle_closure_phase(
                    "skip",
                    None,
                    lambda *args, **kwargs: None,
                    _fake_send_assistant_message,
                )
            )

        self.assertTrue(handled)
        self.assertEqual(fake_chainlit.user_session.get("use_case_feedback_index"), 1)
        self.assertFalse(fake_chainlit.user_session.get("awaiting_use_case_rating"))
        self.assertFalse(fake_chainlit.user_session.get("awaiting_use_case_feasibility"))
        entries = fake_chainlit.user_session.get("use_case_feedback_entries")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["use_case_name"], "Skipped use case")
        self.assertIsNone(entries[0]["rating"])
        self.assertEqual(entries[0]["comment"], "")
        self.assertTrue(any("Next use case" in item["content"] for item in sent))


if __name__ == "__main__":
    unittest.main()
