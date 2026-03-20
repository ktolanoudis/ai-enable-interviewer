import chainlit as cl

from conversation_utils import build_analysis_transcript
from feedback_flow import begin_use_case_feedback, build_use_case_rating_followup, close_interview, parse_use_case_rating, send_next_use_case_feedback_prompt
from meta_question_handler import (
    classify_confirmation_response,
    classify_use_case_feedback_response,
    classify_use_case_scope_resolution,
    generate_use_case_feedback_clarification,
    generate_use_case_feedback_scope_followup,
    generate_use_case_feedback_structural_followup,
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


async def _advance_use_case_feedback_or_close(send_assistant_message, save_checkpoint, message, messages: list, report_payload: dict, feedback_entries: list):
    use_cases = report_payload.get("use_cases") or []
    next_index = int(cl.user_session.get("use_case_feedback_index", 0) or 0) + 1
    cl.user_session.set("use_case_feedback_index", next_index)
    cl.user_session.set("current_use_case_feedback", None)
    cl.user_session.set("awaiting_use_case_opinion", False)
    cl.user_session.set("awaiting_use_case_scope_resolution", False)
    cl.user_session.set("awaiting_use_case_rating", False)

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


async def maybe_handle_company_context_phase(user_input: str, message, save_checkpoint, send_assistant_message) -> bool:
    if cl.user_session.get("awaiting_company_confirmation"):
        messages = cl.user_session.get("messages") or []
        confirmation = classify_confirmation_response(user_input, "company_confirmation", messages)
        if confirmation.get("intent") in {"correction", "no", "other"}:
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

        cl.user_session.set("awaiting_company_confirmation", False)
        cl.user_session.set("company_context_confirmed", True)
        start_prompt = _resume_collection_or_interview()
        if cl.user_session.get("collection_step") is None:
            cl.user_session.set("post_company_confirmation_prompt", "")
        confirmation_msg = "Thanks for confirming.\n\n"
        confirmation_msg += start_prompt or "**What are your main day-to-day tasks?**"

        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": confirmation_msg})
        cl.user_session.set("messages", messages)
        await send_assistant_message(confirmation_msg)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_company_description"):
        cl.user_session.set("awaiting_company_description", False)
        cl.user_session.set("company_context_confirmed", False)
        start_prompt = _start_prompt_from_session()
        messages = cl.user_session.get("messages") or []

        if user_input.lower() != "skip":
            metadata = cl.user_session.get("metadata") or {}
            metadata["company_description_user"] = user_input
            cl.user_session.set("metadata", metadata)
            cl.user_session.set("awaiting_company_description_confirmation", True)
            confirm_msg = (
                "Thanks. Just to confirm: your company "
                f"does the following: {user_input}\n\n"
                "**Is that correct?** (yes/no)"
            )
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": confirm_msg})
            cl.user_session.set("messages", messages)
            await send_assistant_message(confirm_msg)
            save_checkpoint(message)
            return True

        next_prompt = _resume_collection_or_interview()
        if cl.user_session.get("collection_step") is None:
            cl.user_session.set("post_company_confirmation_prompt", "")
        thanks_msg = f"""No problem! We'll continue without company background.

{next_prompt}"""
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": thanks_msg})
        cl.user_session.set("messages", messages)
        await send_assistant_message(thanks_msg)
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
                continue_msg = f"""Great, thanks for confirming.

Now let's begin the interview.

{continue_target}"""
            else:
                continue_msg = f"""Great, thanks for confirming.

{continue_target}"""
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": continue_msg})
            cl.user_session.set("messages", messages)
            await send_assistant_message(continue_msg)
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
    if cl.user_session.get("awaiting_final_confirmation", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        confirmation = classify_confirmation_response(user_input, "final_confirmation", messages)

        if confirmation.get("intent") == "yes":
            cl.user_session.set("awaiting_final_confirmation", False)
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

        follow_up = (
            "Thank you, that's very helpful. "
            "Before I close, is there anything else you'd like to add?"
        )
        messages.append({"role": "assistant", "content": follow_up})
        cl.user_session.set("messages", messages)
        await send_assistant_message(follow_up)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_use_case_feedback_consent", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)
        confirmation = classify_confirmation_response(user_input, "use_case_feedback_consent", messages)

        if confirmation.get("intent") == "yes":
            cl.user_session.set("awaiting_use_case_feedback_consent", False)
            await send_next_use_case_feedback_prompt(send_assistant_message, messages)
            save_checkpoint(message)
            return True

        if confirmation.get("intent") == "no":
            cl.user_session.set("awaiting_use_case_feedback_consent", False)
            await _close_from_state(
                send_assistant_message,
                messages,
                report_payload=cl.user_session.get("pending_report_payload"),
                use_case_feedback=cl.user_session.get("use_case_feedback_entries") or [],
            )
            save_checkpoint(message)
            return True

        retry = "Please answer yes if you'd like to review the suggested AI use cases, or no if you'd prefer to finish now."
        messages.append({"role": "assistant", "content": retry})
        cl.user_session.set("messages", messages)
        await send_assistant_message(retry)
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

        if parsed_rating_at_opinion_step is not None:
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
            followup = generate_use_case_feedback_scope_followup(user_input, use_case_context, messages)
            if followup:
                current_use_case = use_cases[index] if 0 <= index < len(use_cases) else {}
                cl.user_session.set(
                    "current_use_case_feedback",
                    {
                        "use_case_name": current_use_case.get("use_case_name", "AI Use Case"),
                        "description": current_use_case.get("description", ""),
                        "rating": None,
                        "comment": user_input,
                    },
                )
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
            "description": current_use_case.get("description", ""),
            "rating": None,
            "comment": "" if user_input.lower() == "skip" else user_input,
        }
        cl.user_session.set("current_use_case_feedback", current_feedback)
        cl.user_session.set("awaiting_use_case_opinion", False)
        cl.user_session.set("awaiting_use_case_rating", True)

        rating_prompt = build_use_case_rating_followup()
        messages.append({"role": "assistant", "content": rating_prompt})
        cl.user_session.set("messages", messages)
        await send_assistant_message(rating_prompt)
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

        resolution = classify_use_case_scope_resolution(user_input, use_case_context, messages)
        resolution_intent = str(resolution.get("intent", "other")).strip().lower()
        current_feedback = cl.user_session.get("current_use_case_feedback") or {}
        existing_comment = str(current_feedback.get("comment", "")).strip()
        combined_comment = "\n".join(part for part in [existing_comment, user_input] if part).strip()
        current_feedback["comment"] = combined_comment
        cl.user_session.set("current_use_case_feedback", current_feedback)

        if resolution_intent == "outside_role":
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

        if resolution_intent == "low_value":
            cl.user_session.set("awaiting_use_case_scope_resolution", False)
            cl.user_session.set("awaiting_use_case_rating", True)
            rating_prompt = build_use_case_rating_followup()
            messages.append({"role": "assistant", "content": rating_prompt})
            cl.user_session.set("messages", messages)
            await send_assistant_message(rating_prompt)
            save_checkpoint(message)
            return True

        retry = "Should I treat this as outside your role and skip scoring it, or as something within your scope but low-value for your day-to-day work?"
        messages.append({"role": "assistant", "content": retry})
        cl.user_session.set("messages", messages)
        await send_assistant_message(retry)
        save_checkpoint(message)
        return True

    if cl.user_session.get("awaiting_use_case_rating", False):
        messages = cl.user_session.get("messages") or []
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)

        parsed_rating = parse_use_case_rating(user_input)
        if parsed_rating is None:
            retry = "Please rate this use case from 1 to 5, or type 'skip' if you do not want to score it."
            messages.append({"role": "assistant", "content": retry})
            cl.user_session.set("messages", messages)
            await send_assistant_message(retry)
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

        rating_value = None if parsed_rating == "skip" else int(parsed_rating)
        current_feedback = cl.user_session.get("current_use_case_feedback") or {}
        current_feedback["rating"] = rating_value
        cl.user_session.set("current_use_case_feedback", current_feedback)
        cl.user_session.set("awaiting_use_case_rating", False)

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

    return False
