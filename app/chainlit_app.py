import os
import sys
import asyncio
import time
import traceback
import uuid
from dotenv import load_dotenv
import chainlit as cl
from fastapi import Request
from chainlit.server import app as chainlit_server_app

sys.path.append(os.path.dirname(__file__))
load_dotenv(override=False)

from db import init_db, get_interview_checkpoint
from meta_question_handler import (classify_answer_completeness, classify_message_intent, generate_meta_response,
                                   generate_uncertainty_recovery)
from interview_readiness import (
    is_answer_too_short,
)
from conversation_utils import (
    normalize_framework_step,
    thread_is_completed,
)
from checkpoints import (
    active_draft_id,
    ensure_owner_fingerprint,
    fallback_open_draft_checkpoint,
    replay_messages,
    restore_checkpoint_to_session,
    save_checkpoint,
)
from company_flow import (
    collection_prompt_for_step,
    handle_collection_step,
    run_company_setup,
    send_assistant_message,
    send_welcome_prompt,
)
from interview_flow import maybe_handle_closure_phase, maybe_handle_company_context_phase
from question_flow import plan_interview_response
from term_discovery import (
    build_term_clarification_prompt,
    identify_term_candidate,
    lookup_term_context,
    save_term_context,
)
from session_state import (
    OPENAI_MODEL_NAME,
    STOP_ADDENDUM_WINDOW_SECONDS,
    STOP_REMINDER_DELAY_SECONDS,
    WELCOME_TEXT,
    debug_log,
    init_session_state,
)

init_db()


@chainlit_server_app.middleware("http")
async def ensure_anonymous_client_cookie(request: Request, call_next):
    response = await call_next(request)
    if not request.cookies.get("ai_enable_client_id"):
        is_local = request.client and request.client.host in ["127.0.0.1", "localhost"]
        response.set_cookie(
            key="ai_enable_client_id",
            value=str(uuid.uuid4()),
            path="/",
            httponly=False,
            secure=not is_local,
            samesite="lax" if is_local else "none",
            max_age=31536000,
        )
    return response


def _company_setup_needs_resume() -> bool:
    if not cl.user_session.get("company_setup_in_progress"):
        return False
    if cl.user_session.get("interview_started") or cl.user_session.get("report_done"):
        return False
    if (
        cl.user_session.get("awaiting_company_confirmation")
        or cl.user_session.get("awaiting_company_description")
        or cl.user_session.get("awaiting_company_description_confirmation")
    ):
        return False
    return True


async def _restore_checkpoint_if_available(draft_id: str = "", owner: str = "") -> bool:
    if cl.user_session.get("checkpoint_restored_this_connection"):
        return True

    checkpoint = get_interview_checkpoint(draft_id) if draft_id else None
    if not checkpoint and owner:
        checkpoint = fallback_open_draft_checkpoint(owner)
    if not checkpoint or not restore_checkpoint_to_session(checkpoint):
        return False

    messages = cl.user_session.get("messages") or []
    await replay_messages(messages)
    if not messages:
        pending = collection_prompt_for_step(cl.user_session.get("collection_step"))
        if pending:
            if cl.user_session.get("collection_step") == "email":
                await send_welcome_prompt()
            else:
                await send_assistant_message(pending)

    cl.user_session.set("checkpoint_restored_this_connection", True)

    if _company_setup_needs_resume():
        await run_company_setup(save_checkpoint)
    return True


@cl.on_chat_start
async def start():
    """Initialize chat session with company memory"""
    debug_log("session_start", openai_model=OPENAI_MODEL_NAME)

    # If this thread already has a checkpoint, restore instead of resetting.
    owner = ensure_owner_fingerprint()
    draft_id = active_draft_id()
    if await _restore_checkpoint_if_available(draft_id=draft_id, owner=owner):
        return

    existing_messages = cl.user_session.get("messages") or []
    if any(
        isinstance(m, dict)
        and str(m.get("role", "")).strip().lower() == "assistant"
        and str(m.get("content", "")).strip() == WELCOME_TEXT.strip()
        for m in existing_messages
    ):
        return

    # Initialize session state
    init_session_state()
    
    # Send welcome message
    await send_welcome_prompt()
    cl.user_session.set("messages", [{"role": "assistant", "content": WELCOME_TEXT}])
    save_checkpoint()


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
        await send_assistant_message(
            "This interview session is already closed. Start a new chat to run another interview."
        )
        return

    owner = ensure_owner_fingerprint()
    thread_id = active_draft_id(thread=thread)
    await _restore_checkpoint_if_available(draft_id=thread_id, owner=owner)


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
    await send_assistant_message(
        "Take your time. Add anything else when ready, or type 'skip' to continue."
    )


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
    save_checkpoint()
    asyncio.create_task(_send_stop_addendum_reminder(stop_token))


@cl.on_message
async def main(message: cl.Message):
    """Handle incoming user messages with role-awareness and company memory"""
    
    user_input = message.content.strip()
    collection_step = cl.user_session.get("collection_step")
    active_draft_id(message=message)
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
        handled = await handle_collection_step(collection_step, user_input, save_checkpoint, message)
        if handled:
            return
        return
    
    # Interview phase
    if cl.user_session.get("report_done"):
        if not cl.user_session.get("closed_notice_sent", False):
            cl.user_session.set("closed_notice_sent", True)
            await send_assistant_message(
                "This interview session is closed. Start a new chat to run another interview."
            )
            save_checkpoint(message)
        return

    if await maybe_handle_company_context_phase(user_input, message, save_checkpoint, send_assistant_message):
        return

    if await maybe_handle_closure_phase(user_input, message, save_checkpoint, send_assistant_message):
        return

    framework_step = cl.user_session.get("framework_step", "")
    normalized_step = normalize_framework_step(framework_step)
    context = "your work" if not normalized_step else normalized_step.replace("_", " ")
    messages = cl.user_session.get("messages") or []
    message_intent = classify_message_intent(user_input, context, messages)
    intent = str(message_intent.get("intent", "answer")).strip().lower()

    if intent == "clarification":
        meta_response = generate_meta_response(user_input, context, messages)
        if meta_response:
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": meta_response})
            cl.user_session.set("messages", messages)
            await send_assistant_message(meta_response)
            save_checkpoint(message)
            return

    should_skip, alternative_question = (intent == "uncertain"), None

    if should_skip:
        recent_messages = cl.user_session.get("messages") or []
        alternative_question = generate_uncertainty_recovery(
            user_input,
            normalized_step,
            recent_messages,
        )
        if alternative_question:
            stripped = alternative_question.lstrip()
            normalized = stripped.lower().replace("’", "'")
            if not normalized.startswith("that's okay") and not normalized.startswith("thats okay"):
                alternative_question = (
                    "That's okay, we can move on to the next question.\n\n"
                    f"{alternative_question}"
                )

    if should_skip and alternative_question:
        messages = cl.user_session.get("messages")
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": alternative_question})
        cl.user_session.set("messages", messages)
        
        # Update framework step if skipping North Star
        if normalized_step == "north_star":
            cl.user_session.set("framework_step", "step_2_tasks")
        
        await send_assistant_message(alternative_question)
        save_checkpoint(message)
        return
    
    # Input validation (only for non-meta questions)
    if (not stop_addendum_mode) and is_answer_too_short(user_input):
        completeness = classify_answer_completeness(user_input, context, messages)
        if completeness.get("intent") == "too_short":
            await send_assistant_message("Could you provide a bit more detail? (Or type 'skip' to move on)")
            save_checkpoint(message)
            return
    
    # Add user message
    messages = cl.user_session.get("messages")
    if stop_addendum_mode and messages and messages[-1].get("role") == "user":
        messages[-1]["content"] = f"""{messages[-1].get("content", "").rstrip()}

Additional details:
{user_input}"""
    else:
        messages.append({"role": "user", "content": user_input})
    cl.user_session.set("messages", messages)

    if cl.user_session.get("awaiting_term_details", False):
        metadata = cl.user_session.get("metadata") or {}
        term_payload = cl.user_session.get("current_term_candidate") or {}
        metadata = save_term_context(
            metadata,
            str(term_payload.get("term", "")).strip(),
            str(term_payload.get("public_context", "")).strip(),
            user_input,
        )
        cl.user_session.set("metadata", metadata)
        cl.user_session.set("awaiting_term_details", False)
        cl.user_session.set("current_term_candidate", None)
    else:
        metadata = cl.user_session.get("metadata") or {}
        term_candidate = identify_term_candidate(user_input, messages, metadata)
        if term_candidate.get("should_clarify"):
            term = str(term_candidate.get("term", "")).strip()
            public_context = lookup_term_context(term, str(metadata.get("company", "")).strip())
            followup = build_term_clarification_prompt(term, public_context)
            cl.user_session.set("awaiting_term_details", True)
            cl.user_session.set(
                "current_term_candidate",
                {
                    "term": term,
                    "public_context": public_context or "",
                },
            )
            messages.append({"role": "assistant", "content": followup})
            cl.user_session.set("messages", messages)
            await send_assistant_message(followup)
            save_checkpoint(message)
            return
    
    try:
        response = plan_interview_response(messages)
            
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        traceback.print_exc()
        response = error_msg
        messages.append({"role": "assistant", "content": error_msg})
        cl.user_session.set("messages", messages)
    
    await send_assistant_message(response)
    save_checkpoint(message)
