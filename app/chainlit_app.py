import os
import sys
import asyncio
import time
import traceback
from dotenv import load_dotenv
import chainlit as cl

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
from session_state import (
    OPENAI_MODEL_NAME,
    STOP_ADDENDUM_WINDOW_SECONDS,
    STOP_REMINDER_DELAY_SECONDS,
    WELCOME_TEXT,
    debug_log,
    init_session_state,
)

init_db()


@cl.on_chat_start
async def start():
    """Initialize chat session with company memory"""
    debug_log("session_start", openai_model=OPENAI_MODEL_NAME)

    # If this thread already has a checkpoint, restore instead of resetting.
    owner = ensure_owner_fingerprint()
    draft_id = active_draft_id()
    checkpoint = get_interview_checkpoint(draft_id) if draft_id else None
    if not checkpoint:
        checkpoint = fallback_open_draft_checkpoint(owner, "start")
    if checkpoint and restore_checkpoint_to_session(checkpoint):
        messages = cl.user_session.get("messages") or []
        await replay_messages(messages)
        if not messages:
            pending = collection_prompt_for_step(cl.user_session.get("collection_step"))
            if pending:
                await send_assistant_message(pending)
        if (
            cl.user_session.get("company_setup_in_progress")
            and not cl.user_session.get("interview_started")
            and not cl.user_session.get("report_done")
        ):
            await run_company_setup(save_checkpoint)
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
    checkpoint = get_interview_checkpoint(thread_id) if thread_id else None
    if not checkpoint:
        checkpoint = fallback_open_draft_checkpoint(owner, "resume")
    if checkpoint and restore_checkpoint_to_session(checkpoint):
        messages = cl.user_session.get("messages") or []
        await replay_messages(messages)
        if not messages:
            pending = collection_prompt_for_step(cl.user_session.get("collection_step"))
            if pending:
                if cl.user_session.get("collection_step") == "email":
                    await send_welcome_prompt()
                else:
                    await send_assistant_message(pending)
        if (
            cl.user_session.get("company_setup_in_progress")
            and not cl.user_session.get("interview_started")
            and not cl.user_session.get("report_done")
        ):
            await run_company_setup(save_checkpoint)


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
