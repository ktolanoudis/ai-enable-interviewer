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


class SkipFlowTests(unittest.TestCase):
    def test_uncertainty_acknowledgement_is_deduplicated(self):
        response = chainlit_app._with_single_uncertainty_ack(
            "That’s totally okay not to know. We can keep it simple. What part feels most repetitive?",
            "That's okay, we can move on to the next question.",
        )

        self.assertEqual(
            response,
            "That's okay, we can move on to the next question.\n\nWe can keep it simple. What part feels most repetitive?",
        )
        self.assertEqual(response.lower().count("okay"), 1)

    def test_plain_skip_uses_planner_not_adjacent_uncertainty_recovery(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("collection_step", None)
        fake_chainlit.user_session.set("report_done", False)
        fake_chainlit.user_session.set("metadata", {"role": "Associate"})
        fake_chainlit.user_session.set(
            "messages",
            [
                {"role": "assistant", "content": "About how much time do meetings take each week?"},
                {"role": "user", "content": "meeetings"},
                {"role": "assistant", "content": "Could you provide a bit more detail? (Or type 'skip' to move on)"},
            ],
        )
        sent = []

        async def fake_send(content, actions=None):
            sent.append(content)

        with patch.object(chainlit_app, "cl", fake_chainlit), patch.object(
            chainlit_app,
            "classify_message_intent",
            return_value={"intent": "uncertain"},
        ), patch.object(
            chainlit_app,
            "generate_uncertainty_recovery",
            return_value="This should not be used.",
        ) as recovery_mock, patch.object(
            chainlit_app,
            "plan_interview_response",
            return_value="What product work should we focus on next?",
        ) as planner_mock, patch.object(
            chainlit_app,
            "send_assistant_message",
            side_effect=fake_send,
        ), patch.object(
            chainlit_app,
            "maybe_handle_company_context_phase",
            AsyncMock(return_value=False),
        ), patch.object(
            chainlit_app,
            "maybe_handle_closure_phase",
            AsyncMock(return_value=False),
        ), patch.object(
            chainlit_app,
            "active_draft_id",
            return_value="draft_1",
        ), patch.object(
            chainlit_app,
            "save_checkpoint",
            lambda *args, **kwargs: None,
        ):
            asyncio.run(chainlit_app.main(types.SimpleNamespace(content="skip")))

        recovery_mock.assert_not_called()
        planner_mock.assert_called_once()
        planner_messages = planner_mock.call_args.args[0]
        self.assertEqual(planner_messages[-1]["content"], "Skip this question and move to a different topic.")
        self.assertEqual(sent, ["That's okay, we can move on.\n\nWhat product work should we focus on next?"])


if __name__ == "__main__":
    unittest.main()
