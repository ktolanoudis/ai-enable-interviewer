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

import question_flow  # noqa: E402


class _FakeUserSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class QuestionFlowThemeValidationTests(unittest.TestCase):
    def test_plan_interview_response_uses_llm_planner_for_normal_questions(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("metadata", {"role": "Associate", "department": "Operations"})
        fake_chainlit.user_session.set("seniority_level", "intermediate")
        fake_chainlit.user_session.set("interview_count", 0)
        fake_chainlit.user_session.set("company_context", None)
        fake_chainlit.user_session.set("deterministic_ready_streak", 0)
        messages = [
            {"role": "assistant", "content": "**Let's start:** What are your main day-to-day tasks?"},
            {"role": "user", "content": "I coordinate several project workstreams and prepare status updates."},
        ]
        notes = {
            "tasks": [
                {
                    "name": "Coordinate project workstreams",
                    "description": "Coordinate active project workstreams",
                    "manual_steps": [],
                    "time_spent": "",
                    "frequency": "",
                    "friction_level": "",
                    "friction_points": [],
                    "current_systems": [],
                },
                {
                    "name": "Prepare status updates",
                    "description": "Prepare status updates for stakeholders",
                    "manual_steps": [],
                    "time_spent": "",
                    "frequency": "",
                    "friction_level": "",
                    "friction_points": [],
                    "current_systems": [],
                },
            ],
            "business_goals": [],
            "kpis_mentioned": [],
            "ready_for_report": False,
        }

        with patch.object(question_flow, "cl", fake_chainlit), patch.object(
            question_flow,
            "update_notes",
            return_value=notes,
        ), patch.object(
            question_flow,
            "evaluate_notes_readiness",
            return_value={
                "strict_ready": False,
                "turn_limit_ready": False,
                "task_described_count": 2,
                "task_with_friction_count": 0,
                "friction_points_count": 0,
                "goals_or_kpis_count": 0,
                "systems_or_data_count": 0,
                "feasibility_signal_count": 0,
                "llm_ready_for_report": False,
            },
        ), patch.object(
            question_flow,
            "next_question",
            return_value="What would be most useful to understand next about that work?",
        ) as next_question_mock:
            response = question_flow.plan_interview_response(messages)

        self.assertEqual(response, "What would be most useful to understand next about that work?")
        next_question_mock.assert_called_once()
        self.assertFalse(hasattr(question_flow, "phrase_question_for_objective"))

    def test_approval_theme_is_not_covered_by_assistant_company_context(self):
        theme = {
            "theme_key": "approval_bottlenecks",
            "category": "approval",
            "label": "Approval bottlenecks",
        }
        notes = {
            "tasks": [
                {
                    "name": "Coordinate cross-team rollout input",
                    "current_systems": [
                        "shared documents",
                        "spreadsheets",
                        "chat",
                        "internal workflow/ticketing system",
                    ],
                    "friction_points": [
                        "Information comes in too late",
                        "Conflicting input must be consolidated manually",
                    ],
                }
            ]
        }
        messages = [
            {"role": "assistant", "content": "What company do you work for?"},
            {"role": "user", "content": "HelioGrid"},
            {
                "role": "assistant",
                "content": (
                    "HelioGrid offers subsidy assistance and approval/paperwork support "
                    "under the PM Surya Ghar scheme."
                ),
            },
            {"role": "assistant", "content": "**Let's start:** What are your main day-to-day tasks?"},
            {
                "role": "user",
                "content": (
                    "I coordinate rollout updates across teams and work across shared docs, "
                    "spreadsheets, chat, slide decks, and our workflow system."
                ),
            },
        ]
        metadata = {"company": "HelioGrid", "department": "Operations", "role": "Operations manager"}

        with patch.object(question_flow, "assess_theme_alignment", return_value=[]):
            self.assertFalse(question_flow._theme_is_already_covered(theme, notes, messages, metadata))

    def test_next_theme_validation_targets_approval_when_tooling_is_already_covered(self):
        company_context = {
            "recurring_themes": [
                {
                    "theme_key": "fragmented_tooling",
                    "category": "tool",
                    "label": "Fragmented research tooling",
                },
                {
                    "theme_key": "approval_bottlenecks",
                    "category": "approval",
                    "label": "Approval bottlenecks",
                },
            ]
        }
        notes = {
            "tasks": [
                {
                    "name": "Coordinate cross-team rollout input",
                    "current_systems": [
                        "shared documents",
                        "spreadsheets",
                        "chat",
                        "slide decks",
                        "internal workflow/ticketing system",
                    ],
                    "friction_points": [
                        "Information comes in too late",
                        "Inputs change after rollout",
                    ],
                }
            ]
        }
        messages = [
            {
                "role": "assistant",
                "content": (
                    "HelioGrid offers subsidy assistance and approval/paperwork support "
                    "under the PM Surya Ghar scheme."
                ),
            },
            {"role": "assistant", "content": "**Let's start:** What are your main day-to-day tasks?"},
            {
                "role": "user",
                "content": (
                    "I move between shared documents, spreadsheets, chat, slide decks, and "
                    "our internal workflow system to coordinate rollouts."
                ),
            },
            {
                "role": "user",
                "content": (
                    "The biggest friction is late inputs, conflicting feedback, and teams "
                    "using older versions of process notes."
                ),
            },
        ]
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        metadata = {"company": "HelioGrid", "department": "Operations", "role": "Operations manager"}

        with patch.object(question_flow, "cl", fake_chainlit), patch.object(
            question_flow,
            "assess_theme_relevance",
            return_value=[
                {"theme_key": "fragmented_tooling", "relevance": "relevant", "evidence": "The workflow spans multiple tools."},
                {"theme_key": "approval_bottlenecks", "relevance": "relevant", "evidence": "Approval delays could affect rollout work."},
            ],
        ), patch.object(
            question_flow,
            "assess_theme_alignment",
            return_value=[
                {"theme_key": "fragmented_tooling", "stance": "confirm", "evidence": "The workflow spans multiple tools."},
                {"theme_key": "approval_bottlenecks", "stance": "not_mentioned", "evidence": ""},
            ],
        ):
            theme_validation = question_flow._next_theme_validation_question(
                company_context,
                notes,
                messages,
                metadata,
                "intermediate",
            )

        self.assertEqual(
            theme_validation,
            (
                "approval_bottlenecks",
                "Does approval bottlenecks come up in your work? If so, how?",
            ),
        )

    def test_irrelevant_theme_is_skipped_for_unrelated_role(self):
        company_context = {
            "recurring_themes": [
                {
                    "theme_key": "server_overheating",
                    "category": "infrastructure",
                    "label": "Server overheating",
                }
            ]
        }
        notes = {
            "tasks": [
                {
                    "name": "Coordinate cafeteria prep",
                    "current_systems": ["kitchen checklists", "inventory sheet"],
                    "friction_points": ["Late ingredient deliveries"],
                }
            ]
        }
        messages = [
            {"role": "assistant", "content": "**Let's start:** What are your main day-to-day tasks?"},
            {
                "role": "user",
                "content": "I manage canteen prep, meal service timing, and inventory for the kitchen team.",
            },
        ]
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        metadata = {"company": "HelioGrid", "department": "Canteen", "role": "Canteen supervisor"}

        with patch.object(question_flow, "cl", fake_chainlit), patch.object(
            question_flow,
            "assess_theme_relevance",
            return_value=[
                {
                    "theme_key": "server_overheating",
                    "relevance": "not_relevant",
                    "evidence": "The interview is about canteen operations, not IT infrastructure.",
                }
            ],
        ), patch.object(
            question_flow,
            "assess_theme_alignment",
            return_value=[
                {"theme_key": "server_overheating", "stance": "not_mentioned", "evidence": ""},
            ],
        ):
            theme_validation = question_flow._next_theme_validation_question(
                company_context,
                notes,
                messages,
                metadata,
                "intermediate",
            )

        self.assertIsNone(theme_validation)


if __name__ == "__main__":
    unittest.main()
