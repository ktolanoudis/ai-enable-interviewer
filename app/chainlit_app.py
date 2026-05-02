import os
import sys
import asyncio
import time
import traceback
import uuid
import re
from dotenv import load_dotenv
import chainlit as cl
from fastapi import Request
from chainlit.server import app as chainlit_server_app

sys.path.append(os.path.dirname(__file__))
load_dotenv(override=False)

from db import (
    delete_open_interview_checkpoints_for_owner,
    get_interview_checkpoint,
    init_db,
)
from meta_question_handler import (classify_answer_completeness, classify_confirmation_response, classify_message_intent, generate_meta_response,
                                   generate_uncertainty_recovery)
from interview_readiness import (
    is_answer_too_short,
    looks_like_finish_request,
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
from interview_flow import (
    _close_from_state,
    begin_use_case_feedback,
    maybe_handle_closure_phase,
    maybe_handle_company_context_phase,
    _handle_use_case_rating_submission,
)
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


def _has_restore_suppression_cookie() -> bool:
    try:
        current = getattr(getattr(cl, "context", None), "session", None)
        if current is None:
            return False
        cookie_header = None
        environ = getattr(current, "environ", None)
        if isinstance(environ, dict):
            cookie_header = environ.get("HTTP_COOKIE") or environ.get("cookie")
        if not cookie_header:
            headers = getattr(current, "headers", None)
            if headers is not None:
                getter = getattr(headers, "get", None)
                if callable(getter):
                    cookie_header = getter("cookie") or getter("Cookie")
                elif isinstance(headers, dict):
                    cookie_header = headers.get("cookie") or headers.get("Cookie")
        if not cookie_header:
            return False
        match = re.search(r"(?:^|;\s*)suppress_draft_restore=([^;]+)", str(cookie_header))
        return bool(match and match.group(1).strip())
    except Exception:
        return False


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


def _reset_session_for_fresh_chat() -> None:
    owner_fingerprint = cl.user_session.get("owner_fingerprint")
    owner_identity_source = cl.user_session.get("owner_identity_source")
    init_session_state()
    cl.user_session.set("checkpoint_restored_this_connection", False)
    if owner_fingerprint:
        cl.user_session.set("owner_fingerprint", owner_fingerprint)
    if owner_identity_source:
        cl.user_session.set("owner_identity_source", owner_identity_source)


def _abandon_open_drafts_for_fresh_chat() -> str:
    owner = ensure_owner_fingerprint()
    try:
        deleted_count = delete_open_interview_checkpoints_for_owner(owner)
        debug_log("open_drafts_abandoned_for_new_chat", owner=owner, deleted_count=deleted_count)
    except Exception:
        traceback.print_exc()
    return owner


async def _restore_checkpoint_if_available(draft_id: str = "", owner: str = "", allow_fallback: bool = False) -> bool:
    if cl.user_session.get("checkpoint_restored_this_connection"):
        return True

    checkpoint = get_interview_checkpoint(draft_id) if draft_id else None
    if not checkpoint and owner and allow_fallback:
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

    if cl.user_session.get("chat_start_handled", False):
        return
    cl.user_session.set("chat_start_handled", True)

    # If this thread already has a checkpoint, restore instead of resetting.
    allow_fallback = not _has_restore_suppression_cookie()
    if not allow_fallback:
        debug_log("draft_restore_suppressed", handler="chat_start")
        _abandon_open_drafts_for_fresh_chat()
        _reset_session_for_fresh_chat()
    owner = ensure_owner_fingerprint()
    draft_id = active_draft_id()
    if await _restore_checkpoint_if_available(draft_id=draft_id, owner=owner, allow_fallback=allow_fallback):
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
    cl.user_session.set("chat_start_handled", True)
    
    # Send welcome message
    cl.user_session.set("welcome_sent", True)
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

    if _has_restore_suppression_cookie():
        debug_log("draft_restore_suppressed", handler="chat_resume")
        _abandon_open_drafts_for_fresh_chat()
        _reset_session_for_fresh_chat()
        ensure_owner_fingerprint()
        cl.user_session.set("chat_start_handled", True)
        cl.user_session.set("welcome_sent", True)
        await send_welcome_prompt()
        cl.user_session.set("messages", [{"role": "assistant", "content": WELCOME_TEXT}])
        return

    owner = ensure_owner_fingerprint()
    thread_id = active_draft_id(thread=thread)
    await _restore_checkpoint_if_available(draft_id=thread_id, owner=owner, allow_fallback=True)


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

    metadata = cl.user_session.get("metadata") or {}
    normalized_input = str(user_input or "").strip()
    lowered_input = normalized_input.lower()
    if "focus on" in lowered_input:
        focus_match = re.search(r"(?:just\s+)?focus on\s+([^.!?\n]+)", lowered_input)
        if focus_match:
            metadata["interview_focus"] = focus_match.group(1).strip(" .")
    excluded_matches = re.findall(r"(?:let'?s\s+not\s+focus on|do not focus on|don't focus on)\s+([^.!?\n]+)", lowered_input)
    if excluded_matches:
        existing_excluded = [
            str(item).strip() for item in (metadata.get("out_of_scope_topics") or [])
            if str(item).strip()
        ]
        for match in excluded_matches:
            topic = match.strip(" .")
            if topic and topic not in existing_excluded:
                existing_excluded.append(topic)
        metadata["out_of_scope_topics"] = existing_excluded
    cl.user_session.set("metadata", metadata)

    if looks_like_finish_request(user_input):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)
        if cl.user_session.get("finalization_failed"):
            await _close_from_state(
                send_assistant_message,
                messages,
                report_payload=cl.user_session.get("pending_report_payload"),
                use_case_feedback=cl.user_session.get("use_case_feedback_entries") or [],
            )
        else:
            feedback_prompt = await begin_use_case_feedback(send_assistant_message, messages, metadata)
            if feedback_prompt is None:
                await _close_from_state(send_assistant_message, messages)
        save_checkpoint(message)
        return

    framework_step = cl.user_session.get("framework_step", "")
    normalized_step = normalize_framework_step(framework_step)
    context = "your work" if not normalized_step else normalized_step.replace("_", " ")
    messages = cl.user_session.get("messages") or []
    if not cl.user_session.get("awaiting_term_details", False):
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
            if str(user_input or "").strip().lower() in {"skip", "pass", "move on"}:
                skip_messages = list(recent_messages)
                skip_messages.append({"role": "user", "content": "Skip this question and move to a different topic."})
                alternative_question = plan_interview_response(skip_messages)
                if alternative_question:
                    alternative_question = f"That's okay, we can move on.\n\n{alternative_question}"
            else:
                alternative_question = generate_uncertainty_recovery(
                    user_input,
                    normalized_step,
                    recent_messages,
                )
            if alternative_question:
                stripped = alternative_question.lstrip()
                normalized = stripped.lower().replace("’", "'")
                acknowledgement_starts = (
                    "that's okay",
                    "thats okay",
                    "it's okay",
                    "its okay",
                    "it's totally okay",
                    "its totally okay",
                    "it is okay",
                    "it is totally okay",
                    "no problem",
                    "no worries",
                    "that is okay",
                    "that is totally okay",
                )
                if not normalized.startswith(acknowledgement_starts):
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
        term = str(term_payload.get("term", "")).strip()
        public_context = str(term_payload.get("public_context", "")).strip()
        awaiting_confirmation = bool(term_payload.get("awaiting_confirmation"))

        if awaiting_confirmation and public_context:
            confirmation = classify_confirmation_response(user_input, "term_confirmation", messages)
            if confirmation.get("intent") == "yes":
                metadata = save_term_context(metadata, term, public_context, public_context)
                cl.user_session.set("metadata", metadata)
                cl.user_session.set("awaiting_term_details", False)
                cl.user_session.set("current_term_candidate", None)
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
                return
            elif confirmation.get("intent") in {"no", "correction"}:
                followup = (
                    f'Understood. What is "{term}" in your workflow, and what do you mainly use it for?'
                )
                cl.user_session.set(
                    "current_term_candidate",
                    {
                        "term": term,
                        "public_context": public_context,
                        "awaiting_confirmation": False,
                    },
                )
                messages.append({"role": "assistant", "content": followup})
                cl.user_session.set("messages", messages)
                await send_assistant_message(followup)
                save_checkpoint(message)
                return
            else:
                followup = (
                    f'I didn\'t catch that. Do you mean yes, "{term}" is the same tool, or no, it means something else in your workflow?'
                )
                messages.append({"role": "assistant", "content": followup})
                cl.user_session.set("messages", messages)
                await send_assistant_message(followup)
                save_checkpoint(message)
                return
        else:
            metadata = save_term_context(
                metadata,
                term,
                public_context,
                user_input,
            )
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("awaiting_term_details", False)
            cl.user_session.set("current_term_candidate", None)
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
            return
    else:
        metadata = cl.user_session.get("metadata") or {}
        term_candidate = identify_term_candidate(user_input, messages, metadata)
        term = str(term_candidate.get("term", "")).strip()
        if term and term_candidate.get("capture_public_context"):
            public_context = lookup_term_context(term, str(metadata.get("company", "")).strip())
            if public_context:
                metadata = save_term_context(metadata, term, public_context, public_context)
                cl.user_session.set("metadata", metadata)
        if term_candidate.get("should_clarify"):
            public_context = lookup_term_context(term, str(metadata.get("company", "")).strip())
            followup = build_term_clarification_prompt(term, public_context)
            cl.user_session.set("awaiting_term_details", True)
            cl.user_session.set(
                "current_term_candidate",
                {
                    "term": term,
                    "public_context": public_context or "",
                    "awaiting_confirmation": bool(public_context),
                },
            )
            messages.append({"role": "assistant", "content": followup})
            cl.user_session.set("messages", messages)
            await send_assistant_message(followup)
            save_checkpoint(message)
            return
    
    # Input validation (only for non-meta questions)
    if (not stop_addendum_mode) and (not cl.user_session.get("awaiting_term_details", False)) and is_answer_too_short(user_input):
        completeness = classify_answer_completeness(user_input, context, messages)
        if completeness.get("intent") == "too_short":
            await send_assistant_message("Could you provide a bit more detail? (Or type 'skip' to move on)")
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


@cl.action_callback("use_case_rating")
async def handle_use_case_rating_action(action):
    rating_value = ""
    payload = getattr(action, "payload", None) or {}
    if isinstance(payload, dict):
        rating_value = str(payload.get("rating", "")).strip()
    if not rating_value:
        rating_value = str(getattr(action, "value", "") or "").strip()
    if not rating_value:
        return
    await _handle_use_case_rating_submission(
        rating_value,
        None,
        save_checkpoint,
        send_assistant_message,
    )


@cl.action_callback("feasibility_rating")
async def handle_feasibility_rating_action(action):
    rating_value = ""
    payload = getattr(action, "payload", None) or {}
    if isinstance(payload, dict):
        rating_value = str(payload.get("rating", "")).strip()
    if not rating_value:
        rating_value = str(getattr(action, "value", "") or "").strip()
    if not rating_value:
        return
    await maybe_handle_closure_phase(
        rating_value,
        None,
        save_checkpoint,
        send_assistant_message,
    )
