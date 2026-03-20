import chainlit as cl

from conversation_utils import avoid_immediate_question_repeat, build_analysis_transcript, has_valid_north_star
from interview_agent import next_question, update_notes
from interview_readiness import count_user_turns, evaluate_notes_readiness
from role_classifier import classify_seniority, should_ask_north_star
from session_state import DEBUG_QUESTION_FLOW, MAX_INTERVIEW_USER_TURNS, READY_STREAK_REQUIRED, debug_log


def _build_company_context(company_insights: dict | None, interview_count: int):
    if not company_insights:
        return None
    return {
        "north_star": company_insights.get("north_star") if has_valid_north_star(company_insights.get("north_star")) else None,
        "previous_tasks": company_insights.get("all_tasks", []),
        "previous_use_cases": company_insights.get("all_use_cases", []),
        "interview_count": interview_count,
    }


def _update_notes(messages: list, metadata: dict, seniority_level: str, company_context: dict | None) -> dict:
    analysis_transcript = build_analysis_transcript(messages, metadata)
    notes = update_notes(analysis_transcript, seniority_level, company_context)

    if metadata.get("role"):
        notes["role"] = metadata["role"]
    if metadata.get("department"):
        notes["department"] = metadata["department"]

    missing = notes.get("missing", [])
    if isinstance(missing, list):
        notes["missing"] = [m for m in missing if m not in {"role", "department"}]

    cl.user_session.set("notes", notes)
    return notes


def plan_interview_response(messages: list) -> str:
    metadata = cl.user_session.get("metadata", {})
    seniority_level = cl.user_session.get("seniority_level", "intermediate")
    interview_count = cl.user_session.get("interview_count", 0)
    company_insights = cl.user_session.get("company_context")
    company_context = _build_company_context(company_insights, interview_count)

    notes = _update_notes(messages, metadata, seniority_level, company_context)
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
            "I have enough information for the main interview. "
            "Before the final review step, do you want to add anything else?"
        )
        cl.user_session.set("awaiting_final_confirmation", True)
        messages.append({"role": "assistant", "content": final_prompt})
        cl.user_session.set("messages", messages)
        return final_prompt

    role = str(metadata.get("role") or "").strip()
    seniority = classify_seniority(role or "associate")
    use_case_validation_done = cl.user_session.get("use_case_validation_done", False)
    has_north_star = bool(company_context and company_context.get("north_star"))
    ask_north_star = should_ask_north_star(seniority, has_north_star)
    if DEBUG_QUESTION_FLOW:
        debug_log(
            "question_planning",
            has_north_star=has_north_star,
            ask_north_star=ask_north_star,
            use_case_validation_done=use_case_validation_done,
            message_count=len(messages),
        )

    planned_response = next_question(
        messages,
        notes,
        seniority_level,
        interview_count,
        ask_north_star,
        company_context,
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
    return response
