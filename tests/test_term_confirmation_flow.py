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

import chainlit_app  # noqa: E402


class _FakeUserSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class TermConfirmationFlowTests(unittest.TestCase):
    def test_yes_to_term_confirmation_does_not_trigger_short_answer_prompt(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("collection_step", None)
        fake_chainlit.user_session.set("report_done", False)
        fake_chainlit.user_session.set("metadata", {"company": "Acme"})
        fake_chainlit.user_session.set("awaiting_term_details", True)
        fake_chainlit.user_session.set(
            "current_term_candidate",
            {
                "term": "Maven",
                "public_context": "Public context.",
                "awaiting_confirmation": True,
            },
        )
        fake_chainlit.user_session.set(
            "messages",
            [
                {"role": "assistant", "content": "Is that the same Maven you mean?"},
            ],
        )
        sent = []

        async def fake_send(content, actions=None):
            sent.append(content)

        with patch.object(chainlit_app, "cl", fake_chainlit), patch.object(
            chainlit_app,
            "maybe_handle_company_context_phase",
            AsyncMock(return_value=False),
        ), patch.object(
            chainlit_app,
            "maybe_handle_closure_phase",
            AsyncMock(return_value=False),
        ), patch.object(
            chainlit_app,
            "classify_message_intent",
            return_value={"intent": "answer"},
        ), patch.object(
            chainlit_app,
            "classify_confirmation_response",
            return_value={"intent": "yes"},
        ), patch.object(
            chainlit_app,
            "save_term_context",
            side_effect=lambda metadata, term, public_context, user_explanation: {
                **metadata,
                "term_contexts": [
                    {
                        "term": term,
                        "public_context": public_context,
                        "user_explanation": user_explanation,
                    }
                ],
            },
        ), patch.object(
            chainlit_app,
            "is_answer_too_short",
            return_value=True,
        ) as too_short_mock, patch.object(
            chainlit_app,
            "plan_interview_response",
            return_value="What should we cover next?",
        ) as planner_mock, patch.object(
            chainlit_app,
            "send_assistant_message",
            side_effect=fake_send,
        ), patch.object(
            chainlit_app,
            "active_draft_id",
            return_value="draft_1",
        ), patch.object(
            chainlit_app,
            "save_checkpoint",
            lambda *args, **kwargs: None,
        ):
            asyncio.run(chainlit_app.main(types.SimpleNamespace(content="yes")))

        too_short_mock.assert_not_called()
        planner_mock.assert_called_once()
        self.assertEqual(sent, ["What should we cover next?"])
        self.assertFalse(fake_chainlit.user_session.get("awaiting_term_details"))


if __name__ == "__main__":
    unittest.main()
