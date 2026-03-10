import os
import sys
import datetime
import json
import asyncio
import time
import traceback
from dotenv import load_dotenv
import chainlit as cl

sys.path.append(os.path.dirname(__file__))
load_dotenv(override=True)

from interview_agent import next_question, update_notes
from report_agent import generate_report
from db import (init_db, save_session,
                get_company_interview_count, get_company_insights,
                update_company_insights)
from role_classifier import (classify_seniority, should_ask_north_star,
                            should_validate_use_cases)
from meta_question_handler import (is_meta_question, generate_meta_response,
                                   should_skip_question,
                                   is_correction_signal)
from company_research import research_company, format_company_context
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
READY_STREAK_REQUIRED = 2


def debug_log(event: str, **data):
    """Lightweight opt-in debugging for question-selection flow."""
    if not DEBUG_QUESTION_FLOW:
        return
    try:
        payload = json.dumps(data, ensure_ascii=True, default=str)
    except Exception:
        payload = str(data)
    print(f"[DEBUG_QUESTION_FLOW] {event}: {payload}")


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
    
    # Initialize session state
    cl.user_session.set("collection_step", "name")
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
    
    # Send welcome message
    welcome = """# Welcome!

This interview follows a research-based framework to identify AI opportunities.

**Let's get started!**

**What's your name?** (Type 'skip' or 'anonymous' to remain anonymous)"""
    
    await cl.Message(content=welcome).send()


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
    asyncio.create_task(_send_stop_addendum_reminder(stop_token))


@cl.on_message
async def main(message: cl.Message):
    """Handle incoming user messages with role-awareness and company memory"""
    
    user_input = message.content.strip()
    user_message_already_recorded = False
    collection_step = cl.user_session.get("collection_step")
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
        
        if collection_step == "name":
            if user_input.lower() in ['skip', 'anonymous', '']:
                metadata["employee_name"] = "Anonymous"
            else:
                metadata["employee_name"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "department")
            await cl.Message(content="**What department do you work in?**").send()
            return
            
        elif collection_step == "department":
            if not user_input:
                await cl.Message(content="Department is required.").send()
                return
            metadata["department"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "role")
            await cl.Message(content="**What's your position/role?**").send()
            return
            
        elif collection_step == "role":
            if not user_input:
                await cl.Message(content="Position is required.").send()
                return
            metadata["role"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", "company")
            await cl.Message(content="**What company do you work for?**").send()
            return
            
        elif collection_step == "company":
            # Guard against duplicate company-setup runs (e.g. task interruption/retry).
            if cl.user_session.get("interview_started"):
                cl.user_session.set("collection_step", None)
                return
            if cl.user_session.get("company_setup_in_progress"):
                return

            if not user_input:
                await cl.Message(content="Company is required.").send()
                return
            metadata["company"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("collection_step", None)
            cl.user_session.set("company_setup_in_progress", True)
            
            try:
                # Research company online
                await cl.Message(content="Let me look up your company...").send()
                company_info = research_company(metadata["company"], use_ai=True)
                
                # Load company context from previous interviews
                interview_count = get_company_interview_count(metadata["company"])
                company_insights = get_company_insights(metadata["company"])
                
                # Company research is shown in chat only.
                # North Star must come from interview content, not auto company lookup.
                
                cl.user_session.set("interview_count", interview_count)
                cl.user_session.set("company_context", company_insights)
                cl.user_session.set("company_research", company_info)
                
                # Classify seniority from role
                seniority = classify_seniority(metadata["role"])
                cl.user_session.set("seniority_level", seniority.value)
                metadata["seniority_level"] = seniority.value
                cl.user_session.set("metadata", metadata)
                
                # Determine if should ask North Star
                has_north_star = has_valid_north_star(company_insights.get('north_star')) if company_insights else False
                ask_north_star = should_ask_north_star(seniority, has_north_star)
                
                # Build context-aware greeting with company research
                name = metadata['employee_name']
                greeting = f"Thanks, {name}!" if name != "Anonymous" else "Great!"
                greeting += " Now let's begin.\n\n"
                
                # Add company research if found
                if company_info.get('description'):
                    greeting += format_company_context(company_info)
                    greeting += "\n**Is this correct?** (If not, just say 'no' and I'll ask you to describe your company)\n\n"
                    # Set flag to check for correction
                    cl.user_session.set("awaiting_company_confirmation", True)
                
                # Add company context if exists
                if interview_count > 0:
                    greeting += f"As **{seniority.value}-level**, your perspective will help us {get_interview_strategy_description(seniority.value)}.\n\n---\n\n"
                
                # Ask North Star or skip to tasks
                if ask_north_star:
                    cl.user_session.set("framework_step", "step_1_north_star")
                    greeting += "**What are the main business goals or strategic priorities for your organization?**"
                else:
                    cl.user_session.set("framework_step", "step_2_tasks")
                    greeting += "**Let's start:** What are your main day-to-day tasks?"
                
                messages = cl.user_session.get("messages")
                messages.append({"role": "assistant", "content": greeting})
                cl.user_session.set("messages", messages)
                cl.user_session.set("interview_started", True)
                
                await cl.Message(content=greeting).send()
                return
            finally:
                cl.user_session.set("company_setup_in_progress", False)
    
    # Interview phase
    if cl.user_session.get("report_done"):
        if not cl.user_session.get("closed_notice_sent", False):
            cl.user_session.set("closed_notice_sent", True)
            await cl.Message(
                content="This interview session is closed. Start a new chat to run another interview."
            ).send()
        return
    
    # Handle company research correction (if just showed company info)
    if cl.user_session.get("awaiting_company_confirmation"):
        if is_correction_signal(user_input):
            # User says company info is wrong
            cl.user_session.set("awaiting_company_confirmation", False)
            correction_msg = """I apologize for the incorrect information! Let me discard that.

Please briefly describe what your company does (1-2 sentences), or type 'skip' to continue without company context."""
            
            messages = cl.user_session.get("messages")
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": correction_msg})
            cl.user_session.set("messages", messages)
            cl.user_session.set("awaiting_company_description", True)
            
            await cl.Message(content=correction_msg).send()
            return
        else:
            # User confirms or continues - clear flag
            cl.user_session.set("awaiting_company_confirmation", False)
    
    # Handle user providing company description after correction
    if cl.user_session.get("awaiting_company_description"):
        cl.user_session.set("awaiting_company_description", False)
        
        if user_input.lower() != 'skip':
            # Store user's description
            metadata = cl.user_session.get("metadata")
            metadata["company_description_user"] = user_input
            cl.user_session.set("metadata", metadata)
            
            thanks_msg = f"""Thanks for clarifying!

**{metadata['company']}:** {user_input}

---

Now let's begin the interview.

**What are your main day-to-day tasks?**"""
        else:
            thanks_msg = """No problem! We'll continue without company background.

**What are your main day-to-day tasks?**"""
        
        messages = cl.user_session.get("messages")
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": thanks_msg})
        cl.user_session.set("messages", messages)
        
        await cl.Message(content=thanks_msg).send()
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
            return

        if is_no(user_input) or user_input.lower() in {"skip", "done", "that's all", "thats all", "nothing"}:
            cl.user_session.set("messages", messages)
            metadata = cl.user_session.get("metadata", {})
            transcript = "\n".join([f'{m["role"]}: {m["content"]}' for m in messages])
            analysis_transcript = build_analysis_transcript(messages, metadata)
            seniority_level = cl.user_session.get("seniority_level", "intermediate")
            interview_count = cl.user_session.get("interview_count", 0)
            await close_interview(messages, transcript, analysis_transcript, seniority_level, interview_count)
            return

        # Treat non yes/no text as additional addendum and keep close-confirmation loop.
        follow_up = (
            "Thank you, that's very helpful. "
            "Before I close, is there anything else you'd like to add?"
        )
        messages.append({"role": "assistant", "content": follow_up})
        cl.user_session.set("messages", messages)
        await cl.Message(content=follow_up).send()
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
        return
    
    # Input validation (only for non-meta questions)
    if (not user_message_already_recorded) and (not stop_addendum_mode) and is_answer_too_short(user_input) and not is_yes(user_input) and user_input.lower() != 'skip':
        await cl.Message(content="Could you provide a bit more detail? (Or type 'skip' to move on)").send()
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
