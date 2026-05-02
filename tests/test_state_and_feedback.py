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

import feedback_flow  # noqa: E402
import interview_flow  # noqa: E402


class _FakeUserSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class StateAndFeedbackTests(unittest.TestCase):
    def test_post_interview_survey_url_appends_contributor_key_as_ref(self):
        self.assertEqual(
            feedback_flow.build_post_interview_survey_url(
                "https://www.soscisurvey.de/project/?q=main",
                "contrib_123",
            ),
            "https://www.soscisurvey.de/project/?q=main&r=contrib_123",
        )
        self.assertEqual(
            feedback_flow.build_post_interview_survey_url(
                "https://www.soscisurvey.de/project/?q=main&r=old",
                "contrib_123",
            ),
            "https://www.soscisurvey.de/project/?q=main&r=contrib_123",
        )

    def test_final_addendum_goes_to_feedback_not_normal_interview(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("awaiting_final_addendum", True)
        fake_chainlit.user_session.set("messages", [{"role": "assistant", "content": "Please add final details."}])
        fake_chainlit.user_session.set("metadata", {"company": "Acme"})
        sent = []

        async def _fake_send(content, actions=None):
            sent.append(content)

        with patch.object(interview_flow, "cl", fake_chainlit), patch.object(
            interview_flow,
            "begin_use_case_feedback",
            AsyncMock(return_value="feedback prompt"),
        ) as feedback_mock:
            handled = asyncio.run(
                interview_flow.maybe_handle_closure_phase(
                    "One final point about data quality.",
                    None,
                    lambda *args, **kwargs: None,
                    _fake_send,
                )
            )

        self.assertTrue(handled)
        self.assertFalse(fake_chainlit.user_session.get("awaiting_final_addendum"))
        feedback_mock.assert_awaited_once()

    def test_close_failure_keeps_draft_open(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("metadata", {"company": "Acme", "employee_name": "Ana"})
        fake_chainlit.user_session.set("session_id", "session_1")
        fake_chainlit.user_session.set("active_draft_id", "owner:session_1")
        messages = []
        sent = []

        async def _fake_send(content, actions=None):
            sent.append(content)

        with patch.object(feedback_flow, "cl", fake_chainlit), patch.object(
            feedback_flow,
            "generate_report",
            side_effect=RuntimeError("model failed"),
        ), patch.object(feedback_flow, "delete_interview_checkpoint") as delete_mock, patch.object(
            feedback_flow.traceback,
            "print_exc",
        ):
            asyncio.run(
                feedback_flow.close_interview(
                    _fake_send,
                    messages,
                    "transcript",
                    "analysis transcript",
                    "intermediate",
                    0,
                )
            )

        self.assertFalse(fake_chainlit.user_session.get("report_done"))
        self.assertTrue(fake_chainlit.user_session.get("finalization_failed"))
        self.assertNotEqual(fake_chainlit.user_session.get("collection_step"), "__closed__")
        delete_mock.assert_not_called()
        self.assertTrue(any("draft is still saved" in item for item in sent))

    def test_structured_feasibility_feedback_is_aggregated(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("owner_fingerprint", "owner_1")
        with patch.object(feedback_flow, "cl", fake_chainlit):
            entries = feedback_flow.build_validated_use_case_entries(
                [
                    {
                        "use_case_name": "Invoice Review Assistant",
                        "task_name": "Review invoices",
                        "ai_solution_type": "LLM",
                        "rating": 4,
                        "comment": "Useful.",
                        "feasibility_feedback": {
                            "data_quality_score": 3,
                            "regulatory_risk": "medium",
                            "explainability_score": 5,
                            "safe_to_pursue": "yes",
                        },
                    }
                ],
                {"employee_name": "Ana", "role": "Analyst", "department": "Finance"},
                contributor={"contributor_key": "contrib_1"},
            )

        self.assertEqual(entries[0]["average_rating"], 4)
        self.assertEqual(entries[0]["average_data_quality_score"], 3)
        self.assertEqual(entries[0]["average_explainability_score"], 5)
        self.assertEqual(entries[0]["regulatory_risk_counts"]["medium"], 1)
        self.assertEqual(entries[0]["safe_to_pursue_counts"]["yes"], 1)


if __name__ == "__main__":
    unittest.main()
