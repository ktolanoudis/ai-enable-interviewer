import datetime
import json
import os

import chainlit as cl

from interview_readiness import count_user_turns, evaluate_notes_readiness

DEBUG_QUESTION_FLOW = os.getenv("DEBUG_QUESTION_FLOW", "").strip().lower() in {"1", "true", "yes", "on"}
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
POST_INTERVIEW_SURVEY_URL = os.getenv("POST_INTERVIEW_SURVEY_URL", "").strip()
POST_INTERVIEW_SURVEY_TEXT = (
    "One last step: please evaluate your interview experience in this short follow-up survey."
)
STOP_ADDENDUM_WINDOW_SECONDS = 300
STOP_REMINDER_DELAY_SECONDS = 150
MAX_INTERVIEW_USER_TURNS = 14
READY_STREAK_REQUIRED = 3

CHECKPOINT_STATE_KEYS = [
    "collection_step",
    "metadata",
    "messages",
    "notes",
    "report_done",
    "framework_step",
    "session_id",
    "seniority_level",
    "interview_count",
    "company_context",
    "company_setup_in_progress",
    "company_setup_token",
    "interview_started",
    "awaiting_addendum_after_stop",
    "last_stop_ts",
    "stop_token",
    "stop_reminder_sent",
    "deterministic_ready_streak",
    "awaiting_final_confirmation",
    "closed_notice_sent",
    "awaiting_company_confirmation",
    "awaiting_company_description",
    "awaiting_company_description_confirmation",
    "company_context_confirmed",
    "post_company_confirmation_prompt",
    "post_company_confirmation_step",
    "awaiting_term_details",
    "current_term_candidate",
    "pending_report_payload",
    "awaiting_use_case_feedback_consent",
    "awaiting_use_case_opinion",
    "awaiting_use_case_scope_resolution",
    "awaiting_use_case_rating",
    "awaiting_use_case_feasibility",
    "use_case_feedback_index",
    "use_case_feedback_entries",
    "current_use_case_feedback",
    "current_use_case_feasibility_scope",
    "active_draft_id",
    "thread_id",
    "owner_fingerprint",
    "welcome_sent",
    "chat_start_handled",
]

WELCOME_TEXT = """# Welcome!

This interview follows a research-based framework to identify AI opportunities.

**Let's get started!**

**What's your name?** (Type 'skip' or 'anonymous' to remain anonymous)"""


def debug_log(event: str, **data):
    if not DEBUG_QUESTION_FLOW:
        return
    try:
        payload = json.dumps(data, ensure_ascii=True, default=str)
    except Exception:
        payload = str(data)
    print(f"[DEBUG_QUESTION_FLOW] {event}: {payload}")


def init_session_state() -> None:
    cl.user_session.set("collection_step", "name")
    cl.user_session.set("metadata", {})
    cl.user_session.set("messages", [])
    cl.user_session.set(
        "notes",
        {
            "missing": [
                "role",
                "department",
                "north_star_context",
                "tasks",
                "friction_points",
                "business_goals",
                "kpis",
                "data_sources",
                "regulatory_concerns",
            ],
            "ready_for_report": False,
        },
    )
    cl.user_session.set("report_done", False)
    cl.user_session.set("framework_step", None)
    cl.user_session.set("session_id", datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    cl.user_session.set("active_draft_id", None)
    cl.user_session.set("thread_id", None)
    cl.user_session.set("seniority_level", None)
    cl.user_session.set("interview_count", 0)
    cl.user_session.set("company_context", None)
    cl.user_session.set("company_setup_in_progress", False)
    cl.user_session.set("company_setup_token", 0)
    cl.user_session.set("interview_started", False)
    cl.user_session.set("awaiting_addendum_after_stop", False)
    cl.user_session.set("last_stop_ts", 0.0)
    cl.user_session.set("stop_token", 0)
    cl.user_session.set("stop_reminder_sent", False)
    cl.user_session.set("deterministic_ready_streak", 0)
    cl.user_session.set("awaiting_final_confirmation", False)
    cl.user_session.set("closed_notice_sent", False)
    cl.user_session.set("awaiting_company_confirmation", False)
    cl.user_session.set("awaiting_company_description", False)
    cl.user_session.set("awaiting_company_description_confirmation", False)
    cl.user_session.set("company_context_confirmed", False)
    cl.user_session.set("post_company_confirmation_prompt", "")
    cl.user_session.set("post_company_confirmation_step", None)
    cl.user_session.set("awaiting_term_details", False)
    cl.user_session.set("current_term_candidate", None)
    cl.user_session.set("pending_report_payload", None)
    cl.user_session.set("awaiting_use_case_feedback_consent", False)
    cl.user_session.set("awaiting_use_case_opinion", False)
    cl.user_session.set("awaiting_use_case_scope_resolution", False)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("awaiting_use_case_feasibility", False)
    cl.user_session.set("use_case_feedback_index", 0)
    cl.user_session.set("use_case_feedback_entries", [])
    cl.user_session.set("current_use_case_feedback", None)
    cl.user_session.set("current_use_case_feasibility_scope", None)
    cl.user_session.set("welcome_sent", False)
    cl.user_session.set("chat_start_handled", False)


def compute_interview_progress() -> float:
    if cl.user_session.get("report_done"):
        return 1.0

    collection_step = cl.user_session.get("collection_step")
    if collection_step and collection_step != "__closed__":
        ordered_steps = ["name", "company", "company_website", "email", "department", "role"]
        try:
            index = ordered_steps.index(str(collection_step))
        except ValueError:
            index = 0
        return min(0.12, 0.02 + (index / max(1, len(ordered_steps) - 1)) * 0.10)

    if (
        cl.user_session.get("awaiting_company_confirmation")
        or cl.user_session.get("awaiting_company_description")
        or cl.user_session.get("awaiting_company_description_confirmation")
    ):
        return 0.14

    if cl.user_session.get("awaiting_final_confirmation"):
        return 0.76

    if cl.user_session.get("awaiting_use_case_feedback_consent"):
        return 0.8

    pending_report_payload = cl.user_session.get("pending_report_payload") or {}
    use_cases = pending_report_payload.get("use_cases") or []
    if use_cases and (
        cl.user_session.get("awaiting_use_case_opinion")
        or cl.user_session.get("awaiting_use_case_scope_resolution")
        or cl.user_session.get("awaiting_use_case_rating")
        or cl.user_session.get("awaiting_use_case_feasibility")
    ):
        index = int(cl.user_session.get("use_case_feedback_index", 0) or 0)
        total = max(1, len(use_cases))
        base = 0.8 + (min(index, total) / total) * 0.16
        if cl.user_session.get("awaiting_use_case_opinion"):
            step_fraction = 0.02
        elif cl.user_session.get("awaiting_use_case_scope_resolution"):
            step_fraction = 0.04
        elif cl.user_session.get("awaiting_use_case_rating"):
            step_fraction = 0.07
        elif cl.user_session.get("awaiting_use_case_feasibility"):
            step_fraction = 0.1
        else:
            step_fraction = 0.0
        return min(0.98, base + step_fraction / total)

    notes = cl.user_session.get("notes") or {}
    seniority_level = cl.user_session.get("seniority_level") or "intermediate"
    readiness = evaluate_notes_readiness(notes, seniority_level)
    thresholds = {
        "executive": {"tasks": 2, "friction_points": 1, "goals_kpis": 2, "factors": 1, "systems_data": 1},
        "senior": {"tasks": 3, "friction_points": 2, "goals_kpis": 2, "factors": 1, "systems_data": 1},
        "intermediate": {"tasks": 5, "friction_points": 3, "goals_kpis": 2, "factors": 1, "systems_data": 1},
        "junior": {"tasks": 4, "friction_points": 2, "goals_kpis": 1, "factors": 1, "systems_data": 1},
        "intern": {"tasks": 4, "friction_points": 2, "goals_kpis": 1, "factors": 1, "systems_data": 1},
    }.get(seniority_level, {"tasks": 4, "friction_points": 2, "goals_kpis": 1, "factors": 1, "systems_data": 1})

    def _ratio(value, target):
        if not target:
            return 1.0
        return min(1.0, float(value) / float(target))

    ratios = [
        1.0 if (notes.get("role") and notes.get("department")) else 0.0,
        _ratio(readiness.get("task_described_count", 0), thresholds["tasks"]),
        _ratio(readiness.get("task_with_friction_count", 0), max(1, thresholds["tasks"] - 1)),
        _ratio(readiness.get("friction_points_count", 0), thresholds["friction_points"]),
        _ratio(readiness.get("goals_or_kpis_count", 0), thresholds["goals_kpis"]),
        _ratio(readiness.get("systems_or_data_count", 0), thresholds["systems_data"]),
        _ratio(readiness.get("feasibility_signal_count", 0), thresholds["factors"]),
    ]
    readiness_ratio = sum(ratios) / len(ratios)
    streak_ratio = min(1.0, float(cl.user_session.get("deterministic_ready_streak", 0) or 0) / max(1, READY_STREAK_REQUIRED))
    turn_ratio = min(1.0, float(count_user_turns(cl.user_session.get("messages") or [])) / max(1, MAX_INTERVIEW_USER_TURNS))
    blended = max(readiness_ratio * 0.86 + streak_ratio * 0.08 + turn_ratio * 0.06, turn_ratio * 0.45)
    return min(0.74, 0.14 + blended * 0.60)
