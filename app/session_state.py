import datetime
import json
import os

import chainlit as cl

DEBUG_QUESTION_FLOW = os.getenv("DEBUG_QUESTION_FLOW", "").strip().lower() in {"1", "true", "yes", "on"}
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
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
    "use_case_validation_done",
    "company_setup_in_progress",
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
    "pending_report_payload",
    "awaiting_use_case_feedback_consent",
    "awaiting_use_case_opinion",
    "awaiting_use_case_scope_resolution",
    "awaiting_use_case_rating",
    "use_case_feedback_index",
    "use_case_feedback_entries",
    "current_use_case_feedback",
    "active_draft_id",
    "thread_id",
    "client_session_id",
    "owner_fingerprint",
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
    cl.user_session.set("use_case_validation_done", False)
    cl.user_session.set("company_setup_in_progress", False)
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
    cl.user_session.set("pending_report_payload", None)
    cl.user_session.set("awaiting_use_case_feedback_consent", False)
    cl.user_session.set("awaiting_use_case_opinion", False)
    cl.user_session.set("awaiting_use_case_scope_resolution", False)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("use_case_feedback_index", 0)
    cl.user_session.set("use_case_feedback_entries", [])
    cl.user_session.set("current_use_case_feedback", None)
