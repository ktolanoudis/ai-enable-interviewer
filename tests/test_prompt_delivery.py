import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import company_flow  # noqa: E402
import feedback_flow  # noqa: E402
from conversation_utils import split_prompt_context  # noqa: E402


class _FakeUserSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class PromptDeliveryTests(unittest.TestCase):
    def test_split_prompt_context_separates_strategy_from_first_question(self):
        lead, prompt = split_prompt_context(
            "As **intermediate-level**, your perspective will help us document detailed workflows.\n\n---\n\n"
            "**Let's start:** What are your main day-to-day tasks?"
        )

        self.assertEqual(
            lead,
            "As **intermediate-level**, your perspective will help us document detailed workflows.",
        )
        self.assertEqual(
            prompt,
            "**Let's start:** What are your main day-to-day tasks?",
        )

    def test_send_assistant_message_does_not_append_progress_markup(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        sent_messages = []

        class FakeMessage:
            def __init__(self, content="", author=None, actions=None, **kwargs):
                self.content = content
                self.author = author
                self.actions = actions or []

            async def send(self):
                sent_messages.append(self)

        fake_chainlit.Message = FakeMessage

        with patch.object(company_flow, "cl", fake_chainlit):
            asyncio.run(company_flow.send_assistant_message("**What's your position/role?**"))

        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0].content, "**What's your position/role?**")
        self.assertNotIn("ai-enable-progress", sent_messages[0].content)
        self.assertNotIn("<span", sent_messages[0].content)
        self.assertNotIn("data-ai-enable-progress", sent_messages[0].content)

    def test_start_interview_with_company_context_sends_prompt_as_standalone_message(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        sent_messages = []
        checkpoint_calls = []

        fake_chainlit.user_session.set(
            "metadata",
            {
                "role": "Operations manager",
                "employee_name": "Marta",
            },
        )
        fake_chainlit.user_session.set("company_context", {})
        fake_chainlit.user_session.set("interview_count", 1)
        fake_chainlit.user_session.set("messages", [])

        async def _fake_send(content: str, actions=None):
            sent_messages.append((content, actions))

        def _fake_save_checkpoint(message=None):
            checkpoint_calls.append(message)

        with patch.object(company_flow, "cl", fake_chainlit), patch.object(
            company_flow,
            "send_assistant_message",
            _fake_send,
        ):
            asyncio.run(company_flow.start_interview_with_company_context(_fake_save_checkpoint))

        messages = fake_chainlit.user_session.get("messages") or []
        assistant_contents = [m.get("content") for m in messages if m.get("role") == "assistant"]

        self.assertEqual(
            assistant_contents,
            [
                "As **intermediate-level**, your perspective will help us document detailed workflows and practical implementation concerns.",
                "**Let's start:** What are your main day-to-day tasks?",
            ],
        )
        self.assertEqual(
            [content for content, _actions in sent_messages],
            assistant_contents,
        )
        self.assertTrue(fake_chainlit.user_session.get("interview_started"))
        self.assertEqual(len(checkpoint_calls), 1)

    def test_begin_use_case_feedback_sends_only_one_first_prompt_message(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        sent_messages = []

        fake_chainlit.user_session.set(
            "pending_report_payload",
            {
                "use_cases": [
                    {
                        "use_case_name": "Approval Decision Summarization and Jira Update Assistant",
                        "description": "Summarize approval discussions into structured updates.",
                        "expected_impact": "Reduce manual logging time.",
                    }
                ]
            },
        )
        fake_chainlit.user_session.set("messages", [])

        async def _fake_send(content: str, actions=None):
            sent_messages.append((content, actions))

        with patch.object(feedback_flow, "cl", fake_chainlit):
            first_prompt = asyncio.run(
                feedback_flow.begin_use_case_feedback(_fake_send, fake_chainlit.user_session.get("messages"), {})
            )

        messages = fake_chainlit.user_session.get("messages") or []
        assistant_contents = [m.get("content") for m in messages if m.get("role") == "assistant"]

        self.assertEqual(len(assistant_contents), 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(first_prompt, assistant_contents[0])
        self.assertIn("Next, we will review the suggested AI use cases one by one.", first_prompt)
        self.assertIn("Approval Decision Summarization and Jira Update Assistant (1/1)", first_prompt)
        self.assertTrue(fake_chainlit.user_session.get("awaiting_use_case_opinion"))


if __name__ == "__main__":
    unittest.main()
