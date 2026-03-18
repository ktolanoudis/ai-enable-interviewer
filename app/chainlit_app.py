import os
import sys
import datetime
import json
import asyncio
import time
import traceback
import hashlib
import re
from dotenv import load_dotenv
import chainlit as cl

sys.path.append(os.path.dirname(__file__))
load_dotenv(override=False)

from interview_agent import next_question, update_notes
from report_agent import generate_report
from db import (init_db, save_session,
                get_company_interview_count, get_company_insights,
                update_company_insights,
                save_interview_checkpoint, get_interview_checkpoint,
                delete_interview_checkpoint, get_open_interview_checkpoints)
from role_classifier import (classify_seniority, should_ask_north_star,
                            should_validate_use_cases)
from meta_question_handler import (is_meta_question, generate_meta_response,
                                   should_skip_question,
                                   is_correction_signal)
from company_research import research_company, format_company_context, normalize_website_url
from storage import persist_report_files
from report_formatting import generate_markdown_report
from interview_readiness import (
    is_answer_too_short,
    is_yes,
    is_no,
    evaluate_notes_readiness,
    count_user_turns,
)
from conversation_utils import (
    get_interview_strategy_description,
    has_valid_north_star,
    avoid_immediate_question_repeat,
    normalize_framework_step,
    build_analysis_transcript,
    thread_is_completed,
)

init_db()
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
    "post_company_confirmation_prompt",
    "active_draft_id",
    "thread_id",
    "client_session_id",
    "owner_fingerprint",
]
WELCOME_TEXT = """<img src="/public/uni-logo.png" alt="University logo" style="position:fixed; left:16px; bottom:12px; height:40px; width:auto; z-index:2147483647; pointer-events:none;" />
<img src="/public/lab-logo.png" alt="Lab logo" style="position:fixed; right:16px; bottom:12px; height:44px; width:auto; z-index:2147483647; pointer-events:none;" />

# Welcome!

This interview follows a research-based framework to identify AI opportunities.

**Let's get started!**

**What's your work email?**"""
ASSISTANT_AVATAR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "public",
    "uni-logo.png",
)

def debug_log(event: str, **data):
    """Lightweight opt-in debugging for question-selection flow."""
    if not DEBUG_QUESTION_FLOW:
        return
    try:
        payload = json.dumps(data, ensure_ascii=True, default=str)
    except Exception:
        payload = str(data)
    print(f"[DEBUG_QUESTION_FLOW] {event}: {payload}")


def _detect_thread_id(message: cl.Message = None, thread: dict = None) -> str:
    if isinstance(thread, dict):
        for key in ("id", "thread_id"):
            val = thread.get(key)
            if val:
                return str(val)

    if message is not None:
        # Message IDs are per-message and unstable for checkpointing.
        msg_thread_id = getattr(message, "thread_id", None)
        if msg_thread_id:
            return str(msg_thread_id)

    session_thread = cl.user_session.get("thread_id")
    if session_thread:
        return str(session_thread)

    try:
        current = getattr(getattr(cl, "context", None), "session", None)
        thread_val = getattr(current, "thread_id", None)
        if thread_val:
            return str(thread_val)
    except Exception:
        pass
    return ""


def _detect_client_session_id() -> str:
    try:
        current = getattr(getattr(cl, "context", None), "session", None)
        sid = getattr(current, "id", None)
        if sid:
            return str(sid)
    except Exception:
        pass
    return ""


def _detect_owner_fingerprint() -> str:
    candidates = []

    # Chainlit user session may include authenticated user data.
    user_obj = cl.user_session.get("user")
    if isinstance(user_obj, dict):
        for k in ("id", "identifier", "email", "name"):
            v = user_obj.get(k)
            if v:
                candidates.append(str(v))
    elif user_obj is not None:
        for k in ("id", "identifier", "email", "name"):
            v = getattr(user_obj, k, None)
            if v:
                candidates.append(str(v))

    # Best-effort extraction from Chainlit runtime context.
    try:
        current = getattr(getattr(cl, "context", None), "session", None)
        current_user = getattr(current, "user", None)
        if current_user is not None:
            for k in ("id", "identifier", "email", "name"):
                v = getattr(current_user, k, None)
                if v:
                    candidates.append(str(v))

        headers = getattr(current, "headers", None)
        if isinstance(headers, dict):
            for hk in ("x-forwarded-for", "x-real-ip", "user-agent"):
                hv = headers.get(hk) or headers.get(hk.title())
                if hv:
                    candidates.append(str(hv))
    except Exception:
        pass

    raw = "|".join(candidates).strip() or "anonymous"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"owner_{digest}"


def _ensure_owner_fingerprint() -> str:
    existing = cl.user_session.get("owner_fingerprint")
    if existing:
        return str(existing)
    owner = _detect_owner_fingerprint()
    cl.user_session.set("owner_fingerprint", owner)
    return owner


def _is_valid_email(value: str) -> bool:
    if not value:
        return False
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value.strip()) is not None


def _active_draft_id(message: cl.Message = None, thread: dict = None) -> str:
    owner = _ensure_owner_fingerprint()
    thread_id = _detect_thread_id(message=message, thread=thread)
    client_session_id = _detect_client_session_id()
    if thread_id:
        cl.user_session.set("thread_id", thread_id)
    if client_session_id:
        cl.user_session.set("client_session_id", client_session_id)
    session_id = cl.user_session.get("session_id")
    draft_id = (
        thread_id
        or client_session_id
        or cl.user_session.get("active_draft_id")
        or session_id
    )
    if draft_id:
        scoped_id = f"{owner}:{draft_id}"
        cl.user_session.set("active_draft_id", scoped_id)
        return scoped_id
    return ""


def _checkpoint_payload() -> dict:
    state = {}
    for key in CHECKPOINT_STATE_KEYS:
        state[key] = cl.user_session.get(key)
    return {
        "saved_at": datetime.datetime.utcnow().isoformat(),
        "state": state,
    }


def _save_checkpoint(message: cl.Message = None, thread: dict = None) -> None:
    draft_id = _active_draft_id(message=message, thread=thread)
    if not draft_id:
        print("[CHECKPOINT] skip save: no draft_id")
        return
    try:
        if cl.user_session.get("report_done") and cl.user_session.get("collection_step") == "__closed__":
            delete_interview_checkpoint(draft_id)
            print(f"[CHECKPOINT] deleted closed draft_id={draft_id}")
            return
        save_interview_checkpoint(draft_id, _checkpoint_payload())
        print(f"[CHECKPOINT] saved draft_id={draft_id}")
    except Exception:
        print(f"[CHECKPOINT] save failed draft_id={draft_id}")
        traceback.print_exc()


def _restore_checkpoint_to_session(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    state = payload.get("state")
    if not isinstance(state, dict):
        return False
    for key in CHECKPOINT_STATE_KEYS:
        if key in state:
            cl.user_session.set(key, state.get(key))
    return True


async def _replay_messages(messages: list) -> None:
    """Replay stored chat messages to rebuild visible transcript after refresh."""
    if not isinstance(messages, list):
        return
    for m in messages:
        role = str(m.get("role", "")).strip().lower()
        content = str(m.get("content", "")).strip()
        if not content:
            continue
        if role == "assistant":
            author = "Assistant"
        elif role == "user":
            author = "You"
        else:
            author = role.capitalize() if role else "Assistant"
        await cl.Message(content=content, author=author).send()


async def _send_welcome_prompt() -> None:
    await cl.Message(content=WELCOME_TEXT).send()


def _collection_prompt_for_step(step: str) -> str:
    prompts = {
        "email": WELCOME_TEXT,
        "name": "**What's your name?** (Type 'skip' or 'anonymous' to remain anonymous)",
        "department": "**What department do you work in?**",
        "role": "**What's your position/role?**",
        "company": "**What company do you work for?**",
        "company_website": "**What is your company website URL?** (e.g., `https://example.com`, or type 'skip')",
    }
    return prompts.get(step or "", "")


async def _run_company_setup(message: cl.Message = None) -> None:
    """Run company research/setup from already-collected metadata."""
    metadata = cl.user_session.get("metadata") or {}
    company = metadata.get("company")
    role = metadata.get("role")
    if not company or not role:
        return

    cl.user_session.set("company_setup_in_progress", True)
    try:
        await cl.Message(
            content="Let me review your company website and check other online sources..."
        ).send()
        company_info = research_company(
            company,
            company_website=metadata.get("company_website"),
            use_ai=True,
        )

        interview_count = get_company_interview_count(company)
        company_insights = get_company_insights(company)

        cl.user_session.set("interview_count", interview_count)
        cl.user_session.set("company_context", company_insights)

        seniority = classify_seniority(role)
        cl.user_session.set("seniority_level", seniority.value)
        metadata["seniority_level"] = seniority.value

        has_north_star = has_valid_north_star(company_insights.get('north_star')) if company_insights else False
        ask_north_star = should_ask_north_star(seniority, has_north_star)
        if has_north_star:
            metadata["north_star_source_hint"] = "existing_company_memory"
        elif ask_north_star:
            metadata["north_star_source_hint"] = "senior_stakeholder_interview"
        else:
            metadata["north_star_source_hint"] = "inferred_from_online_research"
        cl.user_session.set("metadata", metadata)

        name = metadata.get('employee_name', 'there')
        greeting = f"Thanks, {name}!" if name != "Anonymous" else "Great!"
        greeting += " I reviewed the company context.\n\n"

        interview_start_prompt = ""
        if interview_count > 0:
            interview_start_prompt += (
                f"As **{seniority.value}-level**, your perspective will help us "
                f"{get_interview_strategy_description(seniority.value)}.\n\n---\n\n"
            )

        if ask_north_star:
            cl.user_session.set("framework_step", "step_1_north_star")
            interview_start_prompt += "**What are the main business goals or strategic priorities for your organization?**"
        else:
            cl.user_session.set("framework_step", "step_2_tasks")
            interview_start_prompt += "**Let's start:** What are your main day-to-day tasks?"

        if company_info.get('description'):
            greeting += format_company_context(company_info)
            greeting += (
                "Based on this, it seems your company operates this way.\n"
                "**Is this accurate?** (Say 'yes' to continue or 'no' to correct me.)"
            )
            cl.user_session.set("awaiting_company_confirmation", True)
            cl.user_session.set("post_company_confirmation_prompt", interview_start_prompt)
        else:
            greeting += (
                "I couldn't confidently verify your company context from your website or other public sources.\n"
                "Please describe, in 1-2 sentences, what your company does "
                "(industry, main products/services, and typical customers).\n\n"
                "After that, I'll confirm my understanding and continue the interview."
            )
            cl.user_session.set("awaiting_company_description", True)
            cl.user_session.set("post_company_confirmation_prompt", interview_start_prompt)

        messages = cl.user_session.get("messages") or []
        messages.append({"role": "assistant", "content": greeting})
        cl.user_session.set("messages", messages)
        await cl.Message(content=greeting).send()
        _save_checkpoint(message)
    finally:
        cl.user_session.set("company_setup_in_progress", False)


def _to_epoch_seconds(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.timestamp()
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return 0.0
        # SQLite timestamps often look like "YYYY-MM-DD HH:MM:SS"
        if " " in v and "T" not in v:
            v = v.replace(" ", "T")
        try:
            return datetime.datetime.fromisoformat(v).timestamp()
        except Exception:
            return 0.0
    return 0.0


async def close_interview(messages: list, transcript: str, analysis_transcript: str,
                          seniority_level: str, interview_count: int):
    """Close interview and persist outputs."""
    cl.user_session.set("report_done", True)
    cl.user_session.set("collection_step", "__closed__")
    cl.user_session.set("awaiting_final_confirmation", False)

    closing_msg = (
        "Thank you for your time. "
        "Your answers will be taken into consideration. "
        "This interview is now complete."
    )
    messages.append({"role": "assistant", "content": closing_msg})
    cl.user_session.set("messages", messages)
    await cl.Message(content=closing_msg).send()
    try:
        draft_id = cl.user_session.get("active_draft_id")
        if draft_id:
            delete_interview_checkpoint(str(draft_id))
    except Exception:
        traceback.print_exc()

    try:
        report = generate_report(analysis_transcript)

        session_id = cl.user_session.get("session_id")
        metadata = cl.user_session.get("metadata")
        md_content = generate_markdown_report(report, metadata)
        report_json = report.model_dump_json(indent=2)

        persist_report_files(session_id, report_json, md_content)

        save_session(
            company=metadata["company"],
            employee=metadata["employee_name"],
            department=metadata["department"],
            role=metadata["role"],
            seniority_level=seniority_level,
            transcript=transcript,
            report_json=report_json,
            report_md=md_content
        )

        north_star = None
        if report.north_star_alignment and interview_count == 0:
            north_star = report.north_star_alignment

        update_company_insights(
            company=metadata["company"],
            north_star=north_star,
            tasks=[t.model_dump() for t in report.tasks],
            use_cases=[uc.model_dump() for uc in report.use_cases]
        )
    except Exception:
        traceback.print_exc()
        # Keep interview closed for participant even if backend report generation fails.
        return


@cl.on_chat_start
async def start():
    """Initialize chat session with company memory"""
    debug_log("session_start", openai_model=OPENAI_MODEL_NAME)

    # If this thread already has a checkpoint, restore instead of resetting.
    owner = _ensure_owner_fingerprint()
    draft_id = _active_draft_id()
    print(f"[CHECKPOINT] start draft_id={draft_id}")
    print(f"[CHECKPOINT] start owner={owner}")
    checkpoint = get_interview_checkpoint(draft_id) if draft_id else None
    print(f"[CHECKPOINT] start direct_lookup found={bool(checkpoint)}")
    if not checkpoint:
        open_drafts = get_open_interview_checkpoints(limit=20, owner_fingerprint=owner)
        print(f"[CHECKPOINT] start open_drafts={len(open_drafts)}")
        now_ts = time.time()
        recent = [
            d for d in open_drafts
            if now_ts - _to_epoch_seconds(d.get("updated_at")) <= 1800
        ]
        print(f"[CHECKPOINT] start recent_drafts={len(recent)}")
        if len(recent) == 1:
            checkpoint = recent[0].get("payload")
            restored_draft_id = str(recent[0].get("draft_id") or "")
            if restored_draft_id:
                cl.user_session.set("active_draft_id", restored_draft_id)
            print(f"[CHECKPOINT] start restore recent draft_id={restored_draft_id}")
        elif recent:
            # Prefer latest recent draft to maximize refresh recovery reliability.
            latest_recent = sorted(
                recent,
                key=lambda d: _to_epoch_seconds(d.get("updated_at")),
                reverse=True,
            )[0]
            checkpoint = latest_recent.get("payload")
            restored_draft_id = str(latest_recent.get("draft_id") or "")
            if restored_draft_id:
                cl.user_session.set("active_draft_id", restored_draft_id)
            print(f"[CHECKPOINT] start restore latest_recent draft_id={restored_draft_id}")
        elif open_drafts:
            latest = sorted(
                open_drafts,
                key=lambda d: _to_epoch_seconds(d.get("updated_at")),
                reverse=True,
            )[0]
            checkpoint = latest.get("payload")
            restored_draft_id = str(latest.get("draft_id") or "")
            if restored_draft_id:
                cl.user_session.set("active_draft_id", restored_draft_id)
            print(f"[CHECKPOINT] start restore latest draft_id={restored_draft_id}")
    if checkpoint and _restore_checkpoint_to_session(checkpoint):
        print("[CHECKPOINT] start restored checkpoint")
        messages = cl.user_session.get("messages") or []
        await _replay_messages(messages)
        if not messages:
            pending = _collection_prompt_for_step(cl.user_session.get("collection_step"))
            if pending:
                if cl.user_session.get("collection_step") == "email":
                    await _send_welcome_prompt()
                else:
                    await cl.Message(content=pending).send()
        if (
            cl.user_session.get("company_setup_in_progress")
            and not cl.user_session.get("interview_started")
            and not cl.user_session.get("report_done")
        ):
            print("[CHECKPOINT] start resuming pending company setup")
            await _run_company_setup()
        return

    # Initialize session state
    cl.user_session.set("collection_step", "email")
    cl.user_session.set("metadata", {})
    cl.user_session.set("messages", [])
    cl.user_session.set("notes", {
        "missing": ["role", "department", "north_star_context", "tasks", "friction_points", 
                    "business_goals", "kpis", "data_sources", "regulatory_concerns"],
        "ready_for_report": False
    })
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
    cl.user_session.set("post_company_confirmation_prompt", "")
    
    # Send welcome message
    await _send_welcome_prompt()
    cl.user_session.set("messages", [{"role": "assistant", "content": WELCOME_TEXT}])
    _save_checkpoint()


@cl.on_chat_resume
async def resume(thread: dict):
    """
    Restore closed-session state on reconnect/resume, instead of restarting
    interview collection flow for completed threads.
    """
    if thread_is_completed(thread):
        cl.user_session.set("collection_step", "__closed__")
        cl.user_session.set("report_done", True)
        cl.user_session.set("awaiting_addendum_after_stop", False)
        await cl.Message(
            content="This interview session is already closed. Start a new chat to run another interview."
        ).send()
        return

    owner = _ensure_owner_fingerprint()
    thread_id = _active_draft_id(thread=thread)
    print(f"[CHECKPOINT] resume draft_id={thread_id}")
    print(f"[CHECKPOINT] resume owner={owner}")
    checkpoint = get_interview_checkpoint(thread_id) if thread_id else None
    print(f"[CHECKPOINT] resume direct_lookup found={bool(checkpoint)}")
    if not checkpoint:
        open_drafts = get_open_interview_checkpoints(limit=20, owner_fingerprint=owner)
        print(f"[CHECKPOINT] resume open_drafts={len(open_drafts)}")
        now_ts = time.time()
        recent = [
            d for d in open_drafts
            if now_ts - _to_epoch_seconds(d.get("updated_at")) <= 1800
        ]
        print(f"[CHECKPOINT] resume recent_drafts={len(recent)}")
        if len(recent) == 1:
            checkpoint = recent[0].get("payload")
            restored_draft_id = str(recent[0].get("draft_id") or "")
            if restored_draft_id:
                cl.user_session.set("active_draft_id", restored_draft_id)
            print(f"[CHECKPOINT] resume restore recent draft_id={restored_draft_id}")
        elif recent:
            latest_recent = sorted(
                recent,
                key=lambda d: _to_epoch_seconds(d.get("updated_at")),
                reverse=True,
            )[0]
            checkpoint = latest_recent.get("payload")
            restored_draft_id = str(latest_recent.get("draft_id") or "")
            if restored_draft_id:
                cl.user_session.set("active_draft_id", restored_draft_id)
            print(f"[CHECKPOINT] resume restore latest_recent draft_id={restored_draft_id}")
        elif open_drafts:
            latest = sorted(
                open_drafts,
                key=lambda d: _to_epoch_seconds(d.get("updated_at")),
                reverse=True,
            )[0]
            checkpoint = latest.get("payload")
            restored_draft_id = str(latest.get("draft_id") or "")
            if restored_draft_id:
                cl.user_session.set("active_draft_id", restored_draft_id)
            print(f"[CHECKPOINT] resume restore latest draft_id={restored_draft_id}")
    if checkpoint and _restore_checkpoint_to_session(checkpoint):
        print("[CHECKPOINT] resume restored checkpoint")
        messages = cl.user_session.get("messages") or []
        await _replay_messages(messages)
        if not messages:
            pending = _collection_prompt_for_step(cl.user_session.get("collection_step"))
            if pending:
                if cl.user_session.get("collection_step") == "email":
                    await _send_welcome_prompt()
                else:
                    await cl.Message(content=pending).send()
        if (
            cl.user_session.get("company_setup_in_progress")
            and not cl.user_session.get("interview_started")
            and not cl.user_session.get("report_done")
        ):
            print("[CHECKPOINT] resume resuming pending company setup")
            await _run_company_setup()


async def _send_stop_addendum_reminder(stop_token: int):
    await asyncio.sleep(STOP_REMINDER_DELAY_SECONDS)

    # Only send reminder for the latest stop event if user is still idle.
    if int(cl.user_session.get("stop_token") or 0) != stop_token:
        return
    if cl.user_session.get("report_done"):
        return
    if not cl.user_session.get("awaiting_addendum_after_stop", False):
        return
    if cl.user_session.get("stop_reminder_sent", False):
        return

    last_stop_ts = float(cl.user_session.get("last_stop_ts") or 0.0)
    if time.time() - last_stop_ts < STOP_REMINDER_DELAY_SECONDS:
        return

    cl.user_session.set("stop_reminder_sent", True)
    await cl.Message(
        content="Take your time. Add anything else when ready, or type 'skip' to continue."
    ).send()


@cl.on_stop
async def on_stop():
    """
    If the user manually stops generation, treat their next message as optional
    addendum to the latest answer (within a short window).
    """
    stop_token = int(cl.user_session.get("stop_token") or 0) + 1
    cl.user_session.set("stop_token", stop_token)
    cl.user_session.set("awaiting_addendum_after_stop", True)
    cl.user_session.set("last_stop_ts", time.time())
    cl.user_session.set("stop_reminder_sent", False)
    _save_checkpoint()
    asyncio.create_task(_send_stop_addendum_reminder(stop_token))


@cl.on_message
async def main(message: cl.Message):
    """Handle incoming user messages with role-awareness and company memory"""
    
    user_input = message.content.strip()
    user_message_already_recorded = False
    collection_step = cl.user_session.get("collection_step")
    _active_draft_id(message=message)
    debug_log("message_received", user_input=user_input, collection_step=collection_step)
    stop_addendum_mode = False
    if (not collection_step) and (not cl.user_session.get("report_done")):
        if cl.user_session.get("awaiting_addendum_after_stop", False):
            last_stop_ts = float(cl.user_session.get("last_stop_ts") or 0.0)
            if time.time() - last_stop_ts <= STOP_ADDENDUM_WINDOW_SECONDS:
                stop_addendum_mode = True
            cl.user_session.set("awaiting_addendum_after_stop", False)
            cl.user_session.set("stop_reminder_sent", False)
    
    # Metadata collection phase
    if collection_step:
        metadata = cl.user_session.get("metadata")
        messages = cl.user_session.get("messages") or []

        if collection_step == "email":
            if not _is_valid_email(user_input):
                await cl.Message(content="Please provide a valid work email address.").send()
                _save_checkpoint(message)
                return
            metadata["email"] = user_input.strip().lower()
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "name")
            next_prompt = "**What's your name?** (Type 'skip' or 'anonymous' to remain anonymous)"
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": next_prompt})
            cl.user_session.set("messages", messages)
            await cl.Message(content=next_prompt).send()
            _save_checkpoint(message)
            return

        if collection_step == "name":
            if user_input.lower() in ['skip', 'anonymous', '']:
                metadata["employee_name"] = "Anonymous"
            else:
                metadata["employee_name"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "department")
            next_prompt = "**What department do you work in?**"
            messages.append({"role": "user", "content": user_input or "anonymous"})
            messages.append({"role": "assistant", "content": next_prompt})
            cl.user_session.set("messages", messages)
            await cl.Message(content=next_prompt).send()
            _save_checkpoint(message)
            return
            
        elif collection_step == "department":
            if not user_input:
                await cl.Message(content="Department is required.").send()
                _save_checkpoint(message)
                return
            metadata["department"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "role")
            next_prompt = "**What's your position/role?**"
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": next_prompt})
            cl.user_session.set("messages", messages)
            await cl.Message(content=next_prompt).send()
            _save_checkpoint(message)
            return
            
        elif collection_step == "role":
            if not user_input:
                await cl.Message(content="Position is required.").send()
                _save_checkpoint(message)
                return
            metadata["role"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "company")
            next_prompt = "**What company do you work for?**"
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": next_prompt})
            cl.user_session.set("messages", messages)
            await cl.Message(content=next_prompt).send()
            _save_checkpoint(message)
            return
            
        elif collection_step == "company":
            if not user_input:
                await cl.Message(content="Company is required.").send()
                _save_checkpoint(message)
                return
            metadata["company"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "company_website")
            next_prompt = "**What is your company website URL?** (e.g., `https://example.com`, or type 'skip')"
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": next_prompt})
            cl.user_session.set("messages", messages)
            await cl.Message(content=next_prompt).send()
            _save_checkpoint(message)
            return

        elif collection_step == "company_website":
            # Guard against duplicate company-setup runs (e.g. task interruption/retry).
            if cl.user_session.get("interview_started"):
                cl.user_session.set("collection_step", None)
                _save_checkpoint(message)
                return
            if cl.user_session.get("company_setup_in_progress"):
                _save_checkpoint(message)
                return

            if not user_input:
                await cl.Message(
                    content="Please provide your company website URL, or type 'skip'."
                ).send()
                _save_checkpoint(message)
                return

            if user_input.lower() == "skip":
                metadata["company_website"] = None
            else:
                normalized_url = normalize_website_url(user_input)
                metadata["company_website"] = normalized_url
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", None)
            messages.append({"role": "user", "content": user_input})
            cl.user_session.set("messages", messages)
            await _run_company_setup(message)
            return
    
    # Interview phase
    if cl.user_session.get("report_done"):
        if not cl.user_session.get("closed_notice_sent", False):
            cl.user_session.set("closed_notice_sent", True)
            await cl.Message(
                content="This interview session is closed. Start a new chat to run another interview."
            ).send()
            _save_checkpoint(message)
        return
    
    # Handle company research correction (if just showed company info)
    if cl.user_session.get("awaiting_company_confirmation"):
        if is_correction_signal(user_input) or (not is_yes(user_input)):
            # User says company info is wrong
            cl.user_session.set("awaiting_company_confirmation", False)
            cl.user_session.set("post_company_confirmation_prompt", "")
            correction_msg = """I apologize for the incorrect information! Let me discard that.

Please briefly describe what your company does (1-2 sentences), or type 'skip' to continue without company context."""
            
            messages = cl.user_session.get("messages")
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": correction_msg})
            cl.user_session.set("messages", messages)
            cl.user_session.set("awaiting_company_description", True)
            
            await cl.Message(content=correction_msg).send()
            _save_checkpoint(message)
            return
        else:
            # User confirms and we can start the interview flow.
            cl.user_session.set("awaiting_company_confirmation", False)
            start_prompt = cl.user_session.get("post_company_confirmation_prompt", "").strip()
            cl.user_session.set("post_company_confirmation_prompt", "")
            confirmation_msg = "Thanks for confirming.\n\n"
            confirmation_msg += start_prompt or "**What are your main day-to-day tasks?**"

            messages = cl.user_session.get("messages")
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": confirmation_msg})
            cl.user_session.set("messages", messages)
            cl.user_session.set("interview_started", True)

            await cl.Message(content=confirmation_msg).send()
            _save_checkpoint(message)
            return
    
    # Handle user providing company description after correction
    if cl.user_session.get("awaiting_company_description"):
        cl.user_session.set("awaiting_company_description", False)
        start_prompt = cl.user_session.get("post_company_confirmation_prompt", "").strip()
        if not start_prompt:
            framework_step = cl.user_session.get("framework_step", "step_2_tasks")
            start_prompt = (
                "**What are the main business goals or strategic priorities for your organization?**"
                if framework_step == "step_1_north_star"
                else "**What are your main day-to-day tasks?**"
            )
        
        if user_input.lower() != 'skip':
            # Store user's description
            metadata = cl.user_session.get("metadata")
            metadata["company_description_user"] = user_input
            cl.user_session.set("metadata", metadata)

            cl.user_session.set("awaiting_company_description_confirmation", True)

            confirm_msg = (
                "Thanks. Just to confirm: your company "
                f"does the following: {user_input}\n\n"
                "**Is that correct?** (yes/no)"
            )

            messages = cl.user_session.get("messages")
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": confirm_msg})
            cl.user_session.set("messages", messages)

            await cl.Message(content=confirm_msg).send()
            _save_checkpoint(message)
            return
        else:
            thanks_msg = f"""No problem! We'll continue without company background.

{start_prompt}"""
        
        messages = cl.user_session.get("messages")
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": thanks_msg})
        cl.user_session.set("messages", messages)
        cl.user_session.set("interview_started", True)
        
        await cl.Message(content=thanks_msg).send()
        _save_checkpoint(message)
        return

    # Handle confirmation of user-provided company description
    if cl.user_session.get("awaiting_company_description_confirmation"):
        start_prompt = cl.user_session.get("post_company_confirmation_prompt", "").strip()
        if not start_prompt:
            framework_step = cl.user_session.get("framework_step", "step_2_tasks")
            start_prompt = (
                "**What are the main business goals or strategic priorities for your organization?**"
                if framework_step == "step_1_north_star"
                else "**What are your main day-to-day tasks?**"
            )

        if is_yes(user_input):
            cl.user_session.set("awaiting_company_description_confirmation", False)
            cl.user_session.set("post_company_confirmation_prompt", "")

            continue_msg = f"""Great, thanks for confirming.

Now let's begin the interview.

{start_prompt}"""
            messages = cl.user_session.get("messages")
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": continue_msg})
            cl.user_session.set("messages", messages)
            cl.user_session.set("interview_started", True)
            await cl.Message(content=continue_msg).send()
            _save_checkpoint(message)
            return

        cl.user_session.set("awaiting_company_description_confirmation", False)
        cl.user_session.set("awaiting_company_description", True)
        retry_msg = (
            "Understood. Please rephrase in 1-2 sentences what your company does "
            "(industry, products/services, and typical customers)."
        )
        messages = cl.user_session.get("messages")
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": retry_msg})
        cl.user_session.set("messages", messages)
        await cl.Message(content=retry_msg).send()
        _save_checkpoint(message)
        return

    # Final confirmation before closing interview.
    if cl.user_session.get("awaiting_final_confirmation", False):
        messages = cl.user_session.get("messages")
        messages.append({"role": "user", "content": user_input})
        user_message_already_recorded = True

        if is_yes(user_input):
            cl.user_session.set("awaiting_final_confirmation", False)
            follow_up = "Please add any final details you want included, and then I will close the interview."
            messages.append({"role": "assistant", "content": follow_up})
            cl.user_session.set("messages", messages)
            await cl.Message(content=follow_up).send()
            _save_checkpoint(message)
            return

        if is_no(user_input) or user_input.lower() in {"skip", "done", "that's all", "thats all", "nothing"}:
            cl.user_session.set("messages", messages)
            metadata = cl.user_session.get("metadata", {})
            transcript = "\n".join([f'{m["role"]}: {m["content"]}' for m in messages])
            analysis_transcript = build_analysis_transcript(messages, metadata)
            seniority_level = cl.user_session.get("seniority_level", "intermediate")
            interview_count = cl.user_session.get("interview_count", 0)
            await close_interview(messages, transcript, analysis_transcript, seniority_level, interview_count)
            _save_checkpoint(message)
            return

        # Treat non yes/no text as additional addendum and keep close-confirmation loop.
        follow_up = (
            "Thank you, that's very helpful. "
            "Before I close, is there anything else you'd like to add?"
        )
        messages.append({"role": "assistant", "content": follow_up})
        cl.user_session.set("messages", messages)
        await cl.Message(content=follow_up).send()
        _save_checkpoint(message)
        return
    
    # Handle meta-questions (user asking for clarification, context)
    if is_meta_question(user_input):
        framework_step = cl.user_session.get("framework_step", "")
        normalized_step = normalize_framework_step(framework_step)
        context = "your work" if not normalized_step else normalized_step.replace("_", " ")
        
        meta_response = generate_meta_response(user_input, context)
        if meta_response:
            messages = cl.user_session.get("messages")
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": meta_response})
            cl.user_session.set("messages", messages)
            await cl.Message(content=meta_response).send()
            _save_checkpoint(message)
            return
    
    # Handle uncertainty signals ("I have no idea", "I don't know")
    framework_step = cl.user_session.get("framework_step", "")
    normalized_step = normalize_framework_step(framework_step)
    should_skip, alternative_question = should_skip_question(user_input, normalized_step)
    
    if should_skip and alternative_question:
        messages = cl.user_session.get("messages")
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": alternative_question})
        cl.user_session.set("messages", messages)
        
        # Update framework step if skipping North Star
        if normalized_step == "north_star":
            cl.user_session.set("framework_step", "step_2_tasks")
        
        await cl.Message(content=alternative_question).send()
        _save_checkpoint(message)
        return
    
    # Input validation (only for non-meta questions)
    if (not user_message_already_recorded) and (not stop_addendum_mode) and is_answer_too_short(user_input) and not is_yes(user_input) and user_input.lower() != 'skip':
        await cl.Message(content="Could you provide a bit more detail? (Or type 'skip' to move on)").send()
        _save_checkpoint(message)
        return
    
    # Add user message
    messages = cl.user_session.get("messages")
    if user_message_already_recorded:
        pass
    elif stop_addendum_mode and messages and messages[-1].get("role") == "user":
        messages[-1]["content"] = f"""{messages[-1].get("content", "").rstrip()}

Additional details:
{user_input}"""
    else:
        messages.append({"role": "user", "content": user_input})
    cl.user_session.set("messages", messages)
    
    try:
        metadata = cl.user_session.get("metadata", {})
        transcript = "\n".join([f'{m["role"]}: {m["content"]}' for m in messages])
        analysis_transcript = build_analysis_transcript(messages, metadata)
        
        # Get context
        seniority_level = cl.user_session.get("seniority_level", "intermediate")
        interview_count = cl.user_session.get("interview_count", 0)
        company_insights = cl.user_session.get("company_context")
        
        # Build company context for agent
        company_context = None
        if company_insights:
            company_context = {
                'north_star': company_insights.get('north_star') if has_valid_north_star(company_insights.get('north_star')) else None,
                'previous_tasks': company_insights.get('all_tasks', []),
                'previous_use_cases': company_insights.get('all_use_cases', []),
                'interview_count': interview_count
            }
        
        # Update notes with context
        notes = update_notes(analysis_transcript, seniority_level, company_context)

        # Metadata is collected before interview and may not be present in transcript.
        # Inject known values so the planner doesn't ask for them again.
        if metadata.get("role"):
            notes["role"] = metadata["role"]
        if metadata.get("department"):
            notes["department"] = metadata["department"]

        missing = notes.get("missing", [])
        if isinstance(missing, list):
            notes["missing"] = [m for m in missing if m not in {"role", "department"}]

        cl.user_session.set("notes", notes)
        debug_log(
            "notes_updated",
            seniority_level=seniority_level,
            interview_count=interview_count,
            ready_for_report=bool(notes.get("ready_for_report")),
            missing=notes.get("missing", []),
            task_count=len(notes.get("tasks", []) if isinstance(notes.get("tasks", []), list) else []),
            business_goals_count=len(notes.get("business_goals", []) if isinstance(notes.get("business_goals", []), list) else []),
        )
        readiness = evaluate_notes_readiness(notes, seniority_level)
        user_turn_count = count_user_turns(messages)
        readiness_streak = int(cl.user_session.get("deterministic_ready_streak", 0))
        readiness_streak = readiness_streak + 1 if readiness["strict_ready"] else 0
        cl.user_session.set("deterministic_ready_streak", readiness_streak)

        close_candidate = readiness_streak >= READY_STREAK_REQUIRED
        turn_limit_candidate = user_turn_count >= MAX_INTERVIEW_USER_TURNS and readiness["turn_limit_ready"]

        debug_log(
            "deterministic_readiness",
            readiness=readiness,
            readiness_streak=readiness_streak,
            close_candidate=close_candidate,
            turn_limit_candidate=turn_limit_candidate,
            user_turn_count=user_turn_count,
        )

        if close_candidate or turn_limit_candidate:
            final_prompt = (
                "I have enough information to wrap up. "
                "Do you want to add anything else before I close the interview?"
            )
            cl.user_session.set("awaiting_final_confirmation", True)
            messages.append({"role": "assistant", "content": final_prompt})
            cl.user_session.set("messages", messages)
            response = final_prompt
        else:
            # Check if should validate use cases
            metadata = cl.user_session.get("metadata")
            seniority = classify_seniority(metadata["role"])
            use_case_validation_done = cl.user_session.get("use_case_validation_done", False)
            
            # Show use cases for validation
            if (not use_case_validation_done and 
                interview_count > 0 and 
                should_validate_use_cases(seniority, interview_count) and
                len(messages) > 6 and
                company_context and company_context.get('previous_use_cases')):
                debug_log(
                    "use_case_validation_prompt",
                    use_case_validation_done=use_case_validation_done,
                    interview_count=interview_count,
                    message_count=len(messages),
                    previous_use_cases_count=len(company_context.get("previous_use_cases", [])),
                )
                
                cl.user_session.set("use_case_validation_done", True)
                
                use_cases = company_context['previous_use_cases'][:5]
                validation_msg = f"""## Use Case Validation

Based on previous interviews, we've identified:

"""
                for i, uc in enumerate(use_cases, 1):
                    validation_msg += f"{i}. **{uc.get('use_case_name', 'AI Use Case')}**\n"
                    validation_msg += f"   - {uc.get('description', '')}\n\n"
                
                validation_msg += "**Which would be most valuable? Any concerns?** (or 'skip')"
                
                messages.append({"role": "assistant", "content": validation_msg})
                cl.user_session.set("messages", messages)
                response = validation_msg
            else:
                # Continue with role-aware questions
                has_north_star = bool(company_context and company_context.get('north_star')) if company_context else False
                ask_north_star = should_ask_north_star(seniority, has_north_star)
                debug_log(
                    "question_planning",
                    has_north_star=has_north_star,
                    ask_north_star=ask_north_star,
                    use_case_validation_done=use_case_validation_done,
                    message_count=len(messages),
                )
                
                planned_response = next_question(
                    messages, notes, seniority_level, interview_count, 
                    ask_north_star, company_context
                )
                response = avoid_immediate_question_repeat(planned_response, messages)
                debug_log(
                    "question_selected",
                    planned_response=planned_response,
                    final_response=response,
                    was_rephrased=planned_response != response,
                )
                messages.append({"role": "assistant", "content": response})
                cl.user_session.set("messages", messages)
            
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        traceback.print_exc()
        response = error_msg
        messages.append({"role": "assistant", "content": error_msg})
        cl.user_session.set("messages", messages)
    
    await cl.Message(content=response).send()
    _save_checkpoint(message)
