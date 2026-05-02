import re

import chainlit as cl

from conversation_utils import build_analysis_transcript, paraphrase_repeated_question, split_prompt_context
from feedback_flow import (
    build_use_case_rating_actions,
    begin_use_case_feedback,
    build_use_case_feasibility_prompt,
    build_use_case_feasibility_rating_actions,
    build_use_case_feasibility_rating_followup,
    build_use_case_rating_followup,
    close_interview,
    parse_use_case_rating,
    send_next_use_case_feedback_prompt,
)
from meta_question_handler import (
    assess_use_case_feasibility_scope,
    classify_confirmation_response,
    classify_use_case_feedback_response,
    classify_use_case_scope_resolution,
    extract_single_feasibility_dimension_feedback,
    generate_use_case_feedback_clarification,
    generate_use_case_feedback_scope_followup,
    generate_use_case_feedback_structural_followup,
    interpret_use_case_opinion_response,
    interpret_use_case_rating_response,
)
from company_flow import collection_prompt_for_step, next_collection_step


def _start_prompt_from_session() -> str:
    start_prompt = cl.user_session.get("post_company_confirmation_prompt", "").strip()
    if start_prompt:
        return start_prompt

    framework_step = cl.user_session.get("framework_step", "step_2_tasks")
    return (
        "**What are the main business goals or strategic priorities for your organization?**"
        if framework_step == "step_1_north_star"
        else "**What are your main day-to-day tasks?**"
    )


def _resume_collection_or_interview() -> str:
    metadata = cl.user_session.get("metadata") or {}
    next_step = cl.user_session.get("post_company_confirmation_step") or next_collection_step(metadata)
    if next_step:
        cl.user_session.set("collection_step", next_step)
        cl.user_session.set("post_company_confirmation_step", None)
        return collection_prompt_for_step(next_step)
    cl.user_session.set("post_company_confirmation_step", None)
    cl.user_session.set("interview_started", True)
    return _start_prompt_from_session()


async def _append_and_send_assistant_messages(send_assistant_message, messages: list, contents: list[str]) -> None:
    for content in contents:
        text = str(content or "").strip()
        if not text:
            continue
        messages.append({"role": "assistant", "content": text})
        cl.user_session.set("messages", messages)
        await send_assistant_message(text)


def _transcripts(messages: list, metadata: dict) -> tuple[str, str]:
    transcript = "\n".join([f'{m["role"]}: {m["content"]}' for m in messages])
    analysis_transcript = build_analysis_transcript(messages, metadata)
    return transcript, analysis_transcript


async def _close_from_state(send_assistant_message, messages: list, report_payload=None, use_case_feedback=None):
    metadata = cl.user_session.get("metadata", {})
    transcript, analysis_transcript = _transcripts(messages, metadata)
    seniority_level = cl.user_session.get("seniority_level", "intermediate")
    interview_count = cl.user_session.get("interview_count", 0)
    await close_interview(
        send_assistant_message,
        messages,
        transcript,
        analysis_transcript,
        seniority_level,
        interview_count,
        report_payload=report_payload,
        use_case_feedback=use_case_feedback,
    )


async def _send_use_case_rating_prompt(send_assistant_message, messages: list, prompt: str):
    actions = build_use_case_rating_actions()
    messages.append({"role": "assistant", "content": prompt})
    cl.user_session.set("messages", messages)
    await send_assistant_message(prompt, actions=actions)


async def _send_feasibility_rating_prompt(send_assistant_message, messages: list, dimension: str):
    prompt = build_use_case_feasibility_rating_followup(dimension)
    actions = build_use_case_feasibility_rating_actions(dimension)
    messages.append({"role": "assistant", "content": prompt})
    cl.user_session.set("messages", messages)
    await send_assistant_message(prompt, actions=actions)


def _parse_feasibility_rating(dimension: str, user_input: str):
    dimension = str(dimension or "").strip().lower()
    text = str(user_input or "").strip().lower()
    if text in {"skip", "pass", "n/a", "na"}:
        return "skip"
    if dimension == "regulatory_risk":
        for value in ("critical", "high", "medium", "low"):
            if re.search(rf"\b{value}\b", text):
                return value
        numeric = re.fullmatch(r"([1-4])(?:\s*/\s*4)?", text)
        if numeric:
            return {"1": "low", "2": "medium", "3": "high", "4": "critical"}[numeric.group(1)]
        return None
    return parse_use_case_rating(text)


def _apply_feasibility_rating(feasibility_feedback: dict, dimension: str, rating) -> None:
    if rating == "skip" or rating is None:
        return
    if dimension == "data_quality":
        feasibility_feedback["data_quality_score"] = int(rating)
    elif dimension == "explainability":
        feasibility_feedback["explainability_score"] = int(rating)
    elif dimension == "regulatory_risk":
        feasibility_feedback["regulatory_risk"] = str(rating)


def _text_resolves_outside_role(user_input: str) -> bool:
    text = str(user_input or "").strip().lower().replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    text = text.replace("ouside", "outside").replace("outisde", "outside")
    markers = (
        "outside",
        "outside my role",
        "outside of my role",
        "outside my scope",
        "not my role",
        "not in my role",
        "not part of my role",
        "not my responsibility",
        "someone else",
        "someone else's",
        "another team",
        "belongs to my manager",
        "manager prewrites",
    )
    return any(marker in text for marker in markers)


def _scope_resolution_from_context(user_input: str, messages: list) -> str:
    text = str(user_input or "").strip().lower()
    if text not in {"yes", "y", "yeah", "yep", "correct", "right", "ok", "okay"}:
        return ""
    last_assistant = ""
    for item in reversed(messages or []):
        if isinstance(item, dict) and str(item.get("role", "")).strip().lower() == "assistant":
            last_assistant = str(item.get("content", "") or "").strip().lower()
            break
    if (
        "outside your role" in last_assistant
        and ("skip" in last_assistant or "skipped" in last_assistant or "outside" in last_assistant)
    ):
        return "outside_role"
    return ""


async def _skip_current_use_case_as_outside_role(
    send_assistant_message,
    save_checkpoint,
    message,
    messages: list,
    report_payload: dict,
    current_feedback: dict,
    user_input: str = "",
):
    existing_comment = str(current_feedback.get("comment", "") or "").strip()
    combined_comment = "\n".join(part for part in [existing_comment, str(user_input or "").strip()] if part).strip()
    current_feedback["comment"] = combined_comment
    current_feedback["rating"] = None
    current_feedback["outside_scope"] = True
    current_feedback["feasibility_feedback"] = current_feedback.get("feasibility_feedback") or {}
    cl.user_session.set("current_use_case_feedback", current_feedback)
    cl.user_session.set("awaiting_use_case_opinion", False)
    cl.user_session.set("awaiting_use_case_scope_resolution", False)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("awaiting_use_case_feasibility", False)
    cl.user_session.set("current_use_case_feasibility_scope", None)

    feedback_entries = cl.user_session.get("use_case_feedback_entries") or []
    feedback_entries.append(current_feedback)
    cl.user_session.set("use_case_feedback_entries", feedback_entries)
    acknowledgement = "Understood. I’ll skip this use case because it is outside your role."
    messages.append({"role": "assistant", "content": acknowledgement})
    cl.user_session.set("messages", messages)
    await send_assistant_message(acknowledgement)
    return await _advance_use_case_feedback_or_close(
        send_assistant_message,
        save_checkpoint,
        message,
        messages,
        report_payload,
        feedback_entries,
    )


async def _handle_use_case_rating_submission(
    user_input: str,
    message,
    save_checkpoint,
    send_assistant_message,
):
    messages = cl.user_session.get("messages") or []
    messages.append({"role": "user", "content": user_input})
    cl.user_session.set("messages", messages)

    parsed_rating = parse_use_case_rating(user_input)
    if parsed_rating is None:
        retry = "Please rate this use case from 1 to 5, or type 'skip' if you do not want to score it."
        retry = paraphrase_repeated_question(
            retry,
            messages,
            fallback="Please give this use case a rating from 1 to 5, or say skip if you do not want to score it.",
        )
        await _send_use_case_rating_prompt(send_assistant_message, messages, retry)
        save_checkpoint(message)
        return True

    report_payload = cl.user_session.get("pending_report_payload") or {}
    use_cases = report_payload.get("use_cases") or []
    index = int(cl.user_session.get("use_case_feedback_index", 0) or 0)
    if index >= len(use_cases):
        await _close_from_state(
            send_assistant_message,
            messages,
            report_payload=report_payload,
            use_case_feedback=cl.user_session.get("use_case_feedback_entries") or [],
        )
        save_checkpoint(message)
        return True

    use_case_context = ""
    if 0 <= index < len(use_cases):
        use_case_context = "\n".join(
            part for part in [
                str(use_cases[index].get("use_case_name", "")).strip(),
                str(use_cases[index].get("description", "")).strip(),
                str(use_cases[index].get("expected_impact", "")).strip(),
            ] if part
        )
    rating_interpretation = interpret_use_case_rating_response(user_input, use_case_context, messages)
    interpreted_rating = rating_interpretation.get("rating")
    rating_comment = str(rating_interpretation.get("comment_text", "") or "").strip()
    effective_rating = interpreted_rating if interpreted_rating is not None else parsed_rating
    rating_value = None if effective_rating == "skip" else int(effective_rating)
    current_feedback = cl.user_session.get("current_use_case_feedback") or {}
    current_feedback["rating"] = rating_value
    existing_comment = str(current_feedback.get("comment", "") or "").strip()
    if rating_comment:
        current_feedback["comment"] = "\n".join(
            part for part in [existing_comment, rating_comment] if part
        ).strip()
    cl.user_session.set("current_use_case_feedback", current_feedback)
    cl.user_session.set("awaiting_use_case_rating", False)
    return await _begin_use_case_feasibility_or_advance(
        send_assistant_message,
        save_checkpoint,
        message,
        messages,
        report_payload,
        use_cases,
        index,
        current_feedback,
    )


async def _advance_use_case_feedback_or_close(send_assistant_message, save_checkpoint, message, messages: list, report_payload: dict, feedback_entries: list):
    use_cases = report_payload.get("use_cases") or []
    next_index = int(cl.user_session.get("use_case_feedback_index", 0) or 0) + 1
    cl.user_session.set("use_case_feedback_index", next_index)
    cl.user_session.set("current_use_case_feedback", None)
    cl.user_session.set("awaiting_use_case_opinion", False)
    cl.user_session.set("awaiting_use_case_scope_resolution", False)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("awaiting_use_case_feasibility", False)
    cl.user_session.set("current_use_case_feasibility_scope", None)

    if next_index < len(use_cases):
        await send_next_use_case_feedback_prompt(send_assistant_message, messages)
        save_checkpoint(message)
        return True

    await _close_from_state(
        send_assistant_message,
        messages,
        report_payload=report_payload,
        use_case_feedback=feedback_entries,
    )
    save_checkpoint(message)
    return True


async def _begin_use_case_feasibility_or_advance(
    send_assistant_message,
    save_checkpoint,
    message,
    messages: list,
    report_payload: dict,
    use_cases: list,
    index: int,
    current_feedback: dict,
):
    metadata = cl.user_session.get("metadata") or {}
    use_case_context = ""
    if 0 <= index < len(use_cases):
        use_case_context = "\n".join(
            part for part in [
                str(use_cases[index].get("use_case_name", "")).strip(),
                str(use_cases[index].get("description", "")).strip(),
                str(use_cases[index].get("expected_impact", "")).strip(),
            ] if part
        )
    scope = assess_use_case_feasibility_scope(use_case_context, metadata, messages)
    dimensions = []
    if scope.get("can_judge_data_quality"):
        dimensions.append("data_quality")
    if scope.get("can_judge_regulatory_risk"):
        dimensions.append("regulatory_risk")
    if scope.get("can_judge_explainability"):
        dimensions.append("explainability")
    if dimensions:
        scope["pending_dimensions"] = dimensions
        scope["current_dimension"] = dimensions[0]
        feasibility_prompt = build_use_case_feasibility_prompt(dimensions[0], is_first=True, variant=index)
        feasibility_prompt = paraphrase_repeated_question(
            feasibility_prompt,
            messages,
            fallback=feasibility_prompt,
        )
        cl.user_session.set("awaiting_use_case_feasibility", True)
        cl.user_session.set("current_use_case_feasibility_scope", scope)
        messages.append({"role": "assistant", "content": feasibility_prompt})
        cl.user_session.set("messages", messages)
        await send_assistant_message(feasibility_prompt)
        save_checkpoint(message)
        return True

    feedback_entries = cl.user_session.get("use_case_feedback_entries") or []
    feedback_entries.append(current_feedback)
    cl.user_session.set("use_case_feedback_entries", feedback_entries)
    return await _advance_use_case_feedback_or_close(
        send_assistant_message,
        save_checkpoint,
        message,
        messages,
        report_payload,
        feedback_entries,
    )


async def maybe_handle_company_context_phase(user_input: str, message, save_checkpoint, send_assistant_message) -> bool:
    if cl.user_session.get("awaiting_company_confirmation"):
        messages = cl.user_session.get("messages") or []
        confirmation = classify_confirmation_response(user_input, "company_confirmation", messages)
        if confirmation.get("intent") in {"correction", "no"}:
            cl.user_session.set("awaiting_company_confirmation", False)
            cl.user_session.set("company_context_confirmed", False)
            cl.user_session.set("post_company_confirmation_prompt", "")
            correction_msg = """I apologize for the incorrect information! Let me discard that.

Please briefly describe what your company does (1-2 sentences), or type 'skip' to continue without company context."""
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": correction_msg})
            cl.user_session.set("messages", messages)
            cl.user_session.set("awaiting_company_description", True)
            await send_assistant_message(correction_msg)
            save_checkpoint(message)
            return True

        if confirmation.get("intent") == "other":
            retry_msg = "I didn't catch that. Do you mean yes, the company description is accurate, or no, it needs correcting?"
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": retry_msg})
            cl.user_session.set("messages", messages)
            await send_assistant_message(retry_msg)
            save_checkpoint(message)
            return True

        cl.user_session.set("awaiting_company_confirmation", False)
        cl.user_session.set("company_context_confirmed", True)
        start_prompt = _resume_collection_or_interview()
        if cl.user_session.get("collection_step") is None:
            cl.user_session.set("post_company_confirmation_prompt", "")
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)
        prompt_lead, prompt_question = split_prompt_context(start_prompt or "**What are your main day-to-day tasks?**")
        await _append_and_send_assistant_messages(
            send_assistant_message,
            messages,
            [
                "Thanks for confirming.",
                prompt_lead,
                prompt_question,
            ],
        )
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_company_description"):
        cl.user_session.set("awaiting_company_description", False)
        cl.user_session.set("company_context_confirmed", False)
        messages = cl.user_session.get("messages") or []

        if user_input.lower() != "skip":
            metadata = cl.user_session.get("metadata") or {}
            metadata["company_description_user"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("awaiting_company_description_confirmation", False)
            cl.user_session.set("company_context_confirmed", True)
            next_prompt = _resume_collection_or_interview()
            if cl.user_session.get("collection_step") is None:
                cl.user_session.set("post_company_confirmation_prompt", "")
            messages.append({"role": "user", "content": user_input})
            cl.user_session.set("messages", messages)
            prompt_lead, prompt_question = split_prompt_context(next_prompt)
            await _append_and_send_assistant_messages(
                send_assistant_message,
                messages,
                [
                    "Thanks.",
                    prompt_lead,
                    prompt_question,
                ],
            )
            save_checkpoint(message)
            return True

        next_prompt = _resume_collection_or_interview()
        if cl.user_session.get("collection_step") is None:
            cl.user_session.set("post_company_confirmation_prompt", "")
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)
        prompt_lead, prompt_question = split_prompt_context(next_prompt)
        await _append_and_send_assistant_messages(
            send_assistant_message,
            messages,
            [
                "No problem! We'll continue without company background.",
                prompt_lead,
                prompt_question,
            ],
        )
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_company_description_confirmation"):
        messages = cl.user_session.get("messages") or []
        confirmation = classify_confirmation_response(user_input, "company_description_confirmation", messages)
        if confirmation.get("intent") == "yes":
            cl.user_session.set("awaiting_company_description_confirmation", False)
            cl.user_session.set("company_context_confirmed", True)
            continue_target = _resume_collection_or_interview()
            if cl.user_session.get("collection_step") is None:
                cl.user_session.set("post_company_confirmation_prompt", "")
                lead_message = "Great, thanks for confirming.\n\nNow let's begin the interview."
            else:
                lead_message = "Great, thanks for confirming."
            messages.append({"role": "user", "content": user_input})
            cl.user_session.set("messages", messages)
            prompt_lead, prompt_question = split_prompt_context(continue_target)
            await _append_and_send_assistant_messages(
                send_assistant_message,
                messages,
                [
                    lead_message,
                    prompt_lead,
                    prompt_question,
                ],
            )
            save_checkpoint(message)
            return True

        if confirmation.get("intent") == "other":
            retry_msg = "I didn't catch that. Do you mean yes, that description is right, or no, you want to rephrase it?"
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": retry_msg})
            cl.user_session.set("messages", messages)
            await send_assistant_message(retry_msg)
            save_checkpoint(message)
            return True

        cl.user_session.set("awaiting_company_description_confirmation", False)
        cl.user_session.set("company_context_confirmed", False)
        cl.user_session.set("awaiting_company_description", True)
        retry_msg = (
            "Understood. Please rephrase in 1-2 sentences what your company does "
            "(industry, products/services, and typical customers)."
        )
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": retry_msg})
        cl.user_session.set("messages", messages)
        await send_assistant_message(retry_msg)
        save_checkpoint(message)
        return True

    return False


async def maybe_handle_closure_phase(user_input: str, message, save_checkpoint, send_assistant_message) -> bool:
    if cl.user_session.get("awaiting_final_addendum", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("awaiting_final_addendum", False)
        cl.user_session.set("messages", messages)
        metadata = cl.user_session.get("metadata", {})
        feedback_prompt = await begin_use_case_feedback(send_assistant_message, messages, metadata)
        if feedback_prompt is None:
            await _close_from_state(send_assistant_message, messages)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_final_confirmation", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        confirmation = classify_confirmation_response(user_input, "final_confirmation", messages)

        if confirmation.get("intent") == "yes":
            cl.user_session.set("awaiting_final_confirmation", False)
            cl.user_session.set("awaiting_final_addendum", True)
            follow_up = "Please add any final details you want included, and then I will close the interview."
            messages.append({"role": "assistant", "content": follow_up})
            cl.user_session.set("messages", messages)
            await send_assistant_message(follow_up)
            save_checkpoint(message)
            return True

        if confirmation.get("intent") == "no":
            cl.user_session.set("awaiting_final_confirmation", False)
            cl.user_session.set("messages", messages)
            metadata = cl.user_session.get("metadata", {})
            feedback_prompt = await begin_use_case_feedback(send_assistant_message, messages, metadata)
            if feedback_prompt is None:
                await _close_from_state(send_assistant_message, messages)
            save_checkpoint(message)
            return True

        if len(str(user_input or "").split()) >= 8:
            follow_up = paraphrase_repeated_question(
                "Thank you, that's very helpful. Before I close, is there anything else you'd like to add?",
                messages,
                fallback="Thanks, that adds useful context. If there is one last point you want included before the final review step, add it now. Otherwise just say no.",
            )
            messages.append({"role": "assistant", "content": follow_up})
            cl.user_session.set("messages", messages)
            await send_assistant_message(follow_up)
            save_checkpoint(message)
            return True

        follow_up = (
            "Thank you, that's very helpful. "
            "Before I close, is there anything else you'd like to add?"
        )
        follow_up = paraphrase_repeated_question(
            follow_up,
            messages,
            fallback="If there is anything else you want included before the final review step, tell me now. Otherwise just say no.",
        )
        messages.append({"role": "assistant", "content": follow_up})
        cl.user_session.set("messages", messages)
        await send_assistant_message(follow_up)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_use_case_feedback_consent", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("awaiting_use_case_feedback_consent", False)
        cl.user_session.set("messages", messages)
        await send_next_use_case_feedback_prompt(send_assistant_message, messages)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_use_case_opinion", False):
        messages = cl.user_session.get("messages") or []
        report_payload = cl.user_session.get("pending_report_payload") or {}
        use_cases = report_payload.get("use_cases") or []
        index = int(cl.user_session.get("use_case_feedback_index", 0) or 0)
        use_case_context = ""
        if 0 <= index < len(use_cases):
            current_use_case = use_cases[index]
            use_case_context = "\n".join(
                part for part in [
                    str(current_use_case.get("use_case_name", "")).strip(),
                    str(current_use_case.get("description", "")).strip(),
                    str(current_use_case.get("expected_impact", "")).strip(),
                ] if part
            )

        intent_result = classify_use_case_feedback_response(user_input, use_case_context, messages)
        intent = str(intent_result.get("intent", "opinion")).strip().lower()
        parsed_rating_at_opinion_step = parse_use_case_rating(user_input)
        opinion_interpretation = interpret_use_case_opinion_response(user_input, use_case_context, messages)
        has_substantive_opinion = bool(opinion_interpretation.get("has_substantive_opinion"))
        extracted_opinion = str(opinion_interpretation.get("opinion_text", "") or "").strip()
        included_rating = opinion_interpretation.get("included_rating")
        skipped_opinion = str(user_input or "").strip().lower() == "skip"

        if skipped_opinion:
            messages.append({"role": "user", "content": user_input})
            cl.user_session.set("messages", messages)

            if index >= len(use_cases):
                await _close_from_state(
                    send_assistant_message,
                    messages,
                    report_payload=report_payload,
                    use_case_feedback=cl.user_session.get("use_case_feedback_entries") or [],
                )
                save_checkpoint(message)
                return True

            current_use_case = use_cases[index]
            current_feedback = {
                "use_case_name": current_use_case.get("use_case_name", "AI Use Case"),
                "task_name": current_use_case.get("task_name", ""),
                "ai_solution_type": current_use_case.get("ai_solution_type", ""),
                "description": current_use_case.get("description", ""),
                "rating": None,
                "comment": "",
                "feasibility_feedback": {},
            }
            cl.user_session.set("current_use_case_feedback", current_feedback)
            cl.user_session.set("awaiting_use_case_opinion", False)
            cl.user_session.set("awaiting_use_case_rating", False)
            cl.user_session.set("awaiting_use_case_feasibility", False)
            cl.user_session.set("current_use_case_feasibility_scope", None)
            feedback_entries = cl.user_session.get("use_case_feedback_entries") or []
            feedback_entries.append(current_feedback)
            cl.user_session.set("use_case_feedback_entries", feedback_entries)
            return await _advance_use_case_feedback_or_close(
                send_assistant_message,
                save_checkpoint,
                message,
                messages,
                report_payload,
                feedback_entries,
            )

        if parsed_rating_at_opinion_step is not None and parsed_rating_at_opinion_step != "skip" and not has_substantive_opinion:
            retry = (
                "Before the rating, please tell me briefly in your own words "
                "how useful or not useful this seems for your work."
            )
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": retry})
            cl.user_session.set("messages", messages)
            await send_assistant_message(retry)
            save_checkpoint(message)
            return True

        if intent == "clarification":
            meta_response = generate_use_case_feedback_clarification(user_input, use_case_context, messages)
            if meta_response:
                messages.append({"role": "user", "content": user_input})
                messages.append({"role": "assistant", "content": meta_response})
                cl.user_session.set("messages", messages)
                await send_assistant_message(meta_response)
                save_checkpoint(message)
                return True

        if intent == "uncertain":
            retry = (
                "No problem. Even a rough reaction is useful here. "
                "Does this seem like something that would actually help in your day-to-day work?"
            )
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": retry})
            cl.user_session.set("messages", messages)
            await send_assistant_message(retry)
            save_checkpoint(message)
            return True

        if intent == "structural_feedback":
            followup = generate_use_case_feedback_structural_followup(user_input, use_case_context, messages)
            if followup:
                messages.append({"role": "user", "content": user_input})
                messages.append({"role": "assistant", "content": followup})
                cl.user_session.set("messages", messages)
                await send_assistant_message(followup)
                save_checkpoint(message)
                return True

        if intent == "scope_mismatch":
            current_use_case = use_cases[index] if 0 <= index < len(use_cases) else {}
            current_feedback = {
                "use_case_name": current_use_case.get("use_case_name", "AI Use Case"),
                "task_name": current_use_case.get("task_name", ""),
                "ai_solution_type": current_use_case.get("ai_solution_type", ""),
                "description": current_use_case.get("description", ""),
                "rating": None,
                "comment": user_input,
                "feasibility_feedback": {},
            }
            if _text_resolves_outside_role(user_input):
                messages.append({"role": "user", "content": user_input})
                cl.user_session.set("messages", messages)
                return await _skip_current_use_case_as_outside_role(
                    send_assistant_message,
                    save_checkpoint,
                    message,
                    messages,
                    report_payload,
                    current_feedback,
                )
            followup = generate_use_case_feedback_scope_followup(user_input, use_case_context, messages)
            if followup:
                cl.user_session.set("current_use_case_feedback", current_feedback)
                cl.user_session.set("awaiting_use_case_scope_resolution", True)
                messages.append({"role": "user", "content": user_input})
                messages.append({"role": "assistant", "content": followup})
                cl.user_session.set("messages", messages)
                await send_assistant_message(followup)
                save_checkpoint(message)
                return True

        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)

        if index >= len(use_cases):
            await _close_from_state(
                send_assistant_message,
                messages,
                report_payload=report_payload,
                use_case_feedback=cl.user_session.get("use_case_feedback_entries") or [],
            )
            save_checkpoint(message)
            return True

        current_use_case = use_cases[index]
        current_feedback = {
            "use_case_name": current_use_case.get("use_case_name", "AI Use Case"),
            "task_name": current_use_case.get("task_name", ""),
            "ai_solution_type": current_use_case.get("ai_solution_type", ""),
            "description": current_use_case.get("description", ""),
            "rating": None,
            "comment": "" if skipped_opinion else (extracted_opinion or user_input),
            "feasibility_feedback": {},
        }
        cl.user_session.set("current_use_case_feedback", current_feedback)
        cl.user_session.set("awaiting_use_case_opinion", False)

        if included_rating == "skip":
            current_feedback["rating"] = None
            cl.user_session.set("current_use_case_feedback", current_feedback)
            return await _begin_use_case_feasibility_or_advance(
                send_assistant_message,
                save_checkpoint,
                message,
                messages,
                report_payload,
                use_cases,
                index,
                current_feedback,
            )

        if included_rating is not None:
            current_feedback["rating"] = None if included_rating == "skip" else int(included_rating)
            cl.user_session.set("current_use_case_feedback", current_feedback)
            return await _begin_use_case_feasibility_or_advance(
                send_assistant_message,
                save_checkpoint,
                message,
                messages,
                report_payload,
                use_cases,
                index,
                current_feedback,
            )

        cl.user_session.set("awaiting_use_case_rating", True)

        rating_prompt = build_use_case_rating_followup()
        await _send_use_case_rating_prompt(send_assistant_message, messages, rating_prompt)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_use_case_scope_resolution", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)
        report_payload = cl.user_session.get("pending_report_payload") or {}
        use_cases = report_payload.get("use_cases") or []
        index = int(cl.user_session.get("use_case_feedback_index", 0) or 0)
        use_case_context = ""
        if 0 <= index < len(use_cases):
            current_use_case = use_cases[index]
            use_case_context = "\n".join(
                part for part in [
                    str(current_use_case.get("use_case_name", "")).strip(),
                    str(current_use_case.get("description", "")).strip(),
                    str(current_use_case.get("expected_impact", "")).strip(),
                ] if part
            )

        resolution_intent = _scope_resolution_from_context(user_input, messages)
        if not resolution_intent:
            resolution = classify_use_case_scope_resolution(user_input, use_case_context, messages)
            resolution_intent = str(resolution.get("intent", "other")).strip().lower()
        current_feedback = cl.user_session.get("current_use_case_feedback") or {}
        existing_comment = str(current_feedback.get("comment", "")).strip()
        combined_comment = "\n".join(part for part in [existing_comment, user_input] if part).strip()
        current_feedback["comment"] = combined_comment
        cl.user_session.set("current_use_case_feedback", current_feedback)

        if resolution_intent == "outside_role":
            return await _skip_current_use_case_as_outside_role(
                send_assistant_message,
                save_checkpoint,
                message,
                messages,
                report_payload,
                current_feedback,
            )

        if resolution_intent == "low_value":
            cl.user_session.set("awaiting_use_case_scope_resolution", False)
            cl.user_session.set("awaiting_use_case_rating", True)
            rating_prompt = build_use_case_rating_followup()
            await _send_use_case_rating_prompt(send_assistant_message, messages, rating_prompt)
            save_checkpoint(message)
            return True

        retry = "Should I treat this as outside your role and skip scoring it, or as something within your scope but low-value for your day-to-day work?"
        messages.append({"role": "assistant", "content": retry})
        cl.user_session.set("messages", messages)
        await send_assistant_message(retry)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_use_case_rating", False):
        return await _handle_use_case_rating_submission(
            user_input,
            message,
            save_checkpoint,
            send_assistant_message,
        )

    if cl.user_session.get("awaiting_use_case_feasibility", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)

        report_payload = cl.user_session.get("pending_report_payload") or {}
        use_cases = report_payload.get("use_cases") or []
        index = int(cl.user_session.get("use_case_feedback_index", 0) or 0)
        use_case_context = ""
        if 0 <= index < len(use_cases):
            current_use_case = use_cases[index]
            use_case_context = "\n".join(
                part for part in [
                    str(current_use_case.get("use_case_name", "")).strip(),
                    str(current_use_case.get("description", "")).strip(),
                    str(current_use_case.get("expected_impact", "")).strip(),
                ] if part
            )

        current_feedback = cl.user_session.get("current_use_case_feedback") or {}
        scope = cl.user_session.get("current_use_case_feasibility_scope") or {}
        feasibility_feedback = current_feedback.get("feasibility_feedback") or {}
        current_dimension = str(scope.get("current_dimension") or "").strip()
        if scope.get("awaiting_rating_for_dimension"):
            parsed_rating = _parse_feasibility_rating(current_dimension, user_input)
            if parsed_rating is None:
                await _send_feasibility_rating_prompt(send_assistant_message, messages, current_dimension)
                save_checkpoint(message)
                return True

            _apply_feasibility_rating(feasibility_feedback, current_dimension, parsed_rating)
            scope["awaiting_rating_for_dimension"] = False

            pending_dimensions = list(scope.get("pending_dimensions") or [])
            if pending_dimensions and pending_dimensions[0] == current_dimension:
                pending_dimensions = pending_dimensions[1:]
            elif current_dimension in pending_dimensions:
                pending_dimensions = [d for d in pending_dimensions if d != current_dimension]

            current_feedback["feasibility_feedback"] = feasibility_feedback
            cl.user_session.set("current_use_case_feedback", current_feedback)

            if pending_dimensions:
                scope["pending_dimensions"] = pending_dimensions
                scope["current_dimension"] = pending_dimensions[0]
                scope["awaiting_rating_for_dimension"] = False
                cl.user_session.set("current_use_case_feasibility_scope", scope)
                next_prompt = build_use_case_feasibility_prompt(pending_dimensions[0], is_first=False, variant=index)
                next_prompt = paraphrase_repeated_question(
                    next_prompt,
                    messages,
                    fallback=next_prompt,
                )
                messages.append({"role": "assistant", "content": next_prompt})
                cl.user_session.set("messages", messages)
                await send_assistant_message(next_prompt)
                save_checkpoint(message)
                return True

            cl.user_session.set("awaiting_use_case_feasibility", False)
            cl.user_session.set("current_use_case_feasibility_scope", None)
            feedback_entries = cl.user_session.get("use_case_feedback_entries") or []
            feedback_entries.append(current_feedback)
            cl.user_session.set("use_case_feedback_entries", feedback_entries)
            return await _advance_use_case_feedback_or_close(
                send_assistant_message,
                save_checkpoint,
                message,
                messages,
                report_payload,
                feedback_entries,
            )

        extracted = extract_single_feasibility_dimension_feedback(
            current_dimension,
            user_input,
            use_case_context,
            messages,
        )
        if current_dimension == "data_quality":
            feasibility_feedback["data_quality_comment"] = extracted.get("comment", "")
            if extracted.get("data_quality_score") is not None:
                feasibility_feedback["data_quality_score"] = extracted.get("data_quality_score")
        elif current_dimension == "regulatory_risk":
            feasibility_feedback["regulatory_comment"] = extracted.get("comment", "")
            if extracted.get("regulatory_risk"):
                feasibility_feedback["regulatory_risk"] = extracted.get("regulatory_risk")
        elif current_dimension == "explainability":
            feasibility_feedback["explainability_comment"] = extracted.get("comment", "")
            if extracted.get("explainability_score") is not None:
                feasibility_feedback["explainability_score"] = extracted.get("explainability_score")
        if extracted.get("safe_to_pursue"):
            feasibility_feedback["safe_to_pursue"] = extracted.get("safe_to_pursue")

        pending_dimensions = list(scope.get("pending_dimensions") or [])
        if pending_dimensions and pending_dimensions[0] == current_dimension:
            pending_dimensions = pending_dimensions[1:]
        elif current_dimension in pending_dimensions:
            pending_dimensions = [d for d in pending_dimensions if d != current_dimension]

        current_feedback["feasibility_feedback"] = feasibility_feedback
        cl.user_session.set("current_use_case_feedback", current_feedback)

        scope["awaiting_rating_for_dimension"] = True
        cl.user_session.set("current_use_case_feasibility_scope", scope)
        await _send_feasibility_rating_prompt(send_assistant_message, messages, current_dimension)
        save_checkpoint(message)
        return True

    return False
