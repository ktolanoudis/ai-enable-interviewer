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

    def test_yes_at_opinion_step_goes_directly_to_rating_prompt(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("awaiting_use_case_opinion", True)
        fake_chainlit.user_session.set("awaiting_use_case_rating", False)
        fake_chainlit.user_session.set("awaiting_use_case_feasibility", False)
        fake_chainlit.user_session.set("use_case_feedback_index", 0)
        fake_chainlit.user_session.set("use_case_feedback_entries", [])
        fake_chainlit.user_session.set(
            "pending_report_payload",
            {
                "use_cases": [
                    {
                        "use_case_name": "Status Extraction",
                        "task_name": "Track status",
                        "ai_solution_type": "summarization",
                        "description": "Summarize threads and suggest status updates.",
                    }
                ]
            },
        )
        fake_chainlit.user_session.set("messages", [{"role": "assistant", "content": "How does this seem?"}])
        sent = []

        async def _fake_send_assistant_message(content, actions=None):
            sent.append({"content": content, "actions": actions or []})

        with patch.object(interview_flow, "cl", fake_chainlit), patch.object(
            interview_flow,
            "classify_use_case_feedback_response",
            return_value={"intent": "uncertain"},
        ), patch.object(
            interview_flow,
            "interpret_use_case_opinion_response",
            return_value={"has_substantive_opinion": False, "opinion_text": "", "included_rating": None},
        ):
            handled = asyncio.run(
                interview_flow.maybe_handle_closure_phase(
                    "yes",
                    None,
                    lambda *args, **kwargs: None,
                    _fake_send_assistant_message,
                )
            )

        self.assertTrue(handled)
        self.assertFalse(fake_chainlit.user_session.get("awaiting_use_case_opinion"))
        self.assertTrue(fake_chainlit.user_session.get("awaiting_use_case_rating"))
        self.assertEqual(
            fake_chainlit.user_session.get("current_use_case_feedback")["comment"],
            "Seems useful for my work.",
        )
        self.assertIn("How would you rate it from 1 to 5", sent[-1]["content"])

    def test_hes_at_opinion_step_is_treated_as_yes_typo(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("awaiting_use_case_opinion", True)
        fake_chainlit.user_session.set("awaiting_use_case_rating", False)
        fake_chainlit.user_session.set("use_case_feedback_index", 0)
        fake_chainlit.user_session.set("use_case_feedback_entries", [])
        fake_chainlit.user_session.set(
            "pending_report_payload",
            {"use_cases": [{"use_case_name": "Status Extraction", "description": "Summarize updates."}]},
        )
        fake_chainlit.user_session.set("messages", [{"role": "assistant", "content": "How does this seem?"}])
        sent = []

        async def _fake_send_assistant_message(content, actions=None):
            sent.append({"content": content, "actions": actions or []})

        with patch.object(interview_flow, "cl", fake_chainlit), patch.object(
            interview_flow,
            "classify_use_case_feedback_response",
            return_value={"intent": "uncertain"},
        ), patch.object(
            interview_flow,
            "interpret_use_case_opinion_response",
            return_value={"has_substantive_opinion": False, "opinion_text": "", "included_rating": None},
        ):
            handled = asyncio.run(
                interview_flow.maybe_handle_closure_phase(
                    "hes",
                    None,
                    lambda *args, **kwargs: None,
                    _fake_send_assistant_message,
                )
            )

        self.assertTrue(handled)
        self.assertTrue(fake_chainlit.user_session.get("awaiting_use_case_rating"))
        self.assertEqual(
            fake_chainlit.user_session.get("current_use_case_feedback")["comment"],
            "Seems useful for my work.",
        )
        self.assertIn("How would you rate it from 1 to 5", sent[-1]["content"])

    def test_skip_at_rating_step_skips_entire_use_case(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("awaiting_use_case_rating", True)
        fake_chainlit.user_session.set("awaiting_use_case_feasibility", False)
        fake_chainlit.user_session.set("use_case_feedback_index", 0)
        fake_chainlit.user_session.set("use_case_feedback_entries", [])
        fake_chainlit.user_session.set("current_use_case_feedback", {"comment": "Seems useful for my work."})
        fake_chainlit.user_session.set(
            "pending_report_payload",
            {
                "use_cases": [
                    {
                        "use_case_name": "Skipped at rating",
                        "task_name": "Track status",
                        "ai_solution_type": "summarization",
                        "description": "Summarize updates.",
                    },
                    {
                        "use_case_name": "Next use case",
                        "description": "Next description",
                    },
                ]
            },
        )
        fake_chainlit.user_session.set("messages", [{"role": "assistant", "content": "How would you rate it?"}])
        sent = []

        async def _fake_send_assistant_message(content, actions=None):
            sent.append({"content": content, "actions": actions or []})

        async def _fake_send_next_use_case_feedback_prompt(send_assistant_message, messages):
            await send_assistant_message("Next use case prompt")
            return "Next use case prompt"

        with patch.object(interview_flow, "cl", fake_chainlit), patch.object(
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
        self.assertEqual(entries[0]["use_case_name"], "Skipped at rating")
        self.assertIsNone(entries[0]["rating"])
        self.assertEqual(entries[0]["feasibility_feedback"], {})
        self.assertTrue(any("Next use case" in item["content"] for item in sent))


if __name__ == "__main__":
    unittest.main()
