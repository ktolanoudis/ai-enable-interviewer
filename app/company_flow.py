import chainlit as cl

from collection_intent import parse_collection_response
from company_research import format_company_context, normalize_website_url, research_company
from conversation_utils import get_interview_strategy_description, has_valid_north_star
from db import get_company_insights, get_company_interview_count
from role_classifier import classify_seniority, should_ask_north_star
from session_state import WELCOME_TEXT


async def send_assistant_message(content: str) -> None:
    await cl.Message(content=content, author="Interviewer").send()


async def send_welcome_prompt() -> None:
    await send_assistant_message(WELCOME_TEXT)


def collection_prompt_for_step(step: str) -> str:
    prompts = {
        "name": WELCOME_TEXT,
        "company": "**What company do you work for?**",
        "company_website": "**What is your company website URL?** (e.g., `https://example.com`, or type 'skip')",
        "email": "**What's your work email?**",
        "department": "**What department do you work in?**",
        "role": "**What's your position/role?**",
    }
    return prompts.get(step or "", "")


def metadata_value_from_intent(field: str, parsed: dict):
    intent = str((parsed or {}).get("intent", "")).strip().lower()
    value = str((parsed or {}).get("value", "")).strip()

    if field == "name":
        if intent in {"skip", "anonymous"}:
            return "Anonymous"
        return value or "Anonymous"
    if field == "company":
        if intent in {"skip", "anonymous"}:
            return None
        return value or None
    if field == "company_website":
        if intent in {"skip", "anonymous"}:
            return None
        return normalize_website_url(value) if value else None
    if field == "email":
        if intent in {"skip", "anonymous"}:
            return ""
        return value.lower() if value else ""
    if field in {"department", "role"}:
        if intent in {"skip", "anonymous"}:
            return ""
        return value
    return value


def next_collection_step(metadata: dict) -> str | None:
    for step, key in (("email", "email"), ("department", "department"), ("role", "role")):
        if not str((metadata or {}).get(key) or "").strip():
            return step
    return None


async def start_interview_with_company_context(save_checkpoint, message: cl.Message = None) -> None:
    metadata = cl.user_session.get("metadata") or {}
    role = metadata.get("role") or ""
    company_insights = cl.user_session.get("company_context") or {}
    interview_count = int(cl.user_session.get("interview_count", 0) or 0)
    seniority = classify_seniority(role or "associate")
    cl.user_session.set("seniority_level", seniority.value)
    metadata["seniority_level"] = seniority.value

    has_north_star = has_valid_north_star(company_insights.get("north_star")) if company_insights else False
    ask_north_star = should_ask_north_star(seniority, has_north_star)
    if has_north_star:
        metadata["north_star_source_hint"] = "existing_company_memory"
    elif ask_north_star:
        metadata["north_star_source_hint"] = "senior_stakeholder_interview"
    else:
        metadata["north_star_source_hint"] = "inferred_from_online_research"
    cl.user_session.set("metadata", metadata)

    prompt = ""
    if interview_count > 0:
        prompt += (
            f"As **{seniority.value}-level**, your perspective will help us "
            f"{get_interview_strategy_description(seniority.value)}.\n\n---\n\n"
        )

    if ask_north_star:
        cl.user_session.set("framework_step", "step_1_north_star")
        prompt += "**What are the main business goals or strategic priorities for your organization?**"
    else:
        cl.user_session.set("framework_step", "step_2_tasks")
        prompt += "**Let's start:** What are your main day-to-day tasks?"

    messages = cl.user_session.get("messages") or []
    messages.append({"role": "assistant", "content": prompt})
    cl.user_session.set("messages", messages)
    cl.user_session.set("interview_started", True)
    await send_assistant_message(prompt)
    save_checkpoint(message)


async def start_interview_without_company_context(save_checkpoint, message: cl.Message = None) -> None:
    metadata = cl.user_session.get("metadata") or {}
    role = metadata.get("role") or ""
    seniority = classify_seniority(role or "associate")
    cl.user_session.set("seniority_level", seniority.value)
    metadata["seniority_level"] = seniority.value
    metadata["north_star_source_hint"] = "not_specified"
    cl.user_session.set("metadata", metadata)
    cl.user_session.set("interview_count", 0)
    cl.user_session.set("company_context", None)

    if should_ask_north_star(seniority, False):
        cl.user_session.set("framework_step", "step_1_north_star")
        prompt = "**What are the main business goals or strategic priorities for your organization?**"
    else:
        cl.user_session.set("framework_step", "step_2_tasks")
        prompt = "**Let's start:** What are your main day-to-day tasks?"

    name = metadata.get("employee_name", "there")
    greeting = f"Thanks, {name}!" if name != "Anonymous" else "Great!"
    greeting += " We'll continue without company background for now.\n\n"
    greeting += prompt

    messages = cl.user_session.get("messages") or []
    messages.append({"role": "assistant", "content": greeting})
    cl.user_session.set("messages", messages)
    cl.user_session.set("interview_started", True)
    await send_assistant_message(greeting)
    save_checkpoint(message)


async def run_company_setup(save_checkpoint, message: cl.Message = None) -> None:
    metadata = cl.user_session.get("metadata") or {}
    company = metadata.get("company")
    role = metadata.get("role")
    if not company:
        await start_interview_without_company_context(save_checkpoint, message)
        return

    if (
        cl.user_session.get("company_context_confirmed")
        or cl.user_session.get("awaiting_company_confirmation")
        or cl.user_session.get("awaiting_company_description")
        or cl.user_session.get("awaiting_company_description_confirmation")
    ):
        return

    setup_token = int(cl.user_session.get("company_setup_token") or 0) + 1
    cl.user_session.set("company_setup_token", setup_token)
    cl.user_session.set("company_setup_in_progress", True)
    try:
        await send_assistant_message("Let me review your company website and check other online sources...")
        company_info = research_company(company, company_website=metadata.get("company_website"), use_ai=True)

        if int(cl.user_session.get("company_setup_token") or 0) != setup_token:
            return
        if (
            cl.user_session.get("company_context_confirmed")
            or cl.user_session.get("awaiting_company_confirmation")
            or cl.user_session.get("awaiting_company_description")
            or cl.user_session.get("awaiting_company_description_confirmation")
        ):
            return

        interview_count = get_company_interview_count(company)
        company_insights = get_company_insights(company)
        cl.user_session.set("interview_count", interview_count)
        cl.user_session.set("company_context", company_insights)

        seniority = classify_seniority(role or "associate")
        cl.user_session.set("seniority_level", seniority.value)
        metadata["seniority_level"] = seniority.value

        has_north_star = has_valid_north_star(company_insights.get("north_star")) if company_insights else False
        ask_north_star = should_ask_north_star(seniority, has_north_star)
        if has_north_star:
            metadata["north_star_source_hint"] = "existing_company_memory"
        elif ask_north_star:
            metadata["north_star_source_hint"] = "senior_stakeholder_interview"
        else:
            metadata["north_star_source_hint"] = "inferred_from_online_research"
        cl.user_session.set("metadata", metadata)

        name = metadata.get("employee_name", "there")
        greeting = f"Thanks, {name}!" if name != "Anonymous" else "Great!"
        greeting += " I reviewed the company context.\n\n"

        interview_start_prompt = ""
        if interview_count > 0:
            interview_start_prompt += (
                f"As **{seniority.value}-level**, your perspective will help us "
                f"{get_interview_strategy_description(seniority.value)}.\n\n---\n\n"
            )

        next_step = next_collection_step(metadata)
        if next_step:
            interview_start_prompt = collection_prompt_for_step(next_step)
            cl.user_session.set("post_company_confirmation_step", next_step)
        else:
            cl.user_session.set("post_company_confirmation_step", None)
            if ask_north_star:
                cl.user_session.set("framework_step", "step_1_north_star")
                interview_start_prompt += "**What are the main business goals or strategic priorities for your organization?**"
            else:
                cl.user_session.set("framework_step", "step_2_tasks")
                interview_start_prompt += "**Let's start:** What are your main day-to-day tasks?"

        if company_info.get("description"):
            greeting += format_company_context(company_info)
            greeting += "Based on this, it seems your company operates this way.\n**Is this accurate?** (Say 'yes' to continue or 'no' to correct me.)"
            cl.user_session.set("awaiting_company_confirmation", True)
            cl.user_session.set("post_company_confirmation_prompt", interview_start_prompt)
        else:
            greeting += (
                "I couldn't confidently verify your company context from your website or other public sources.\n"
                "Please describe, in 1-2 sentences, what your company does (industry, main products/services, and typical customers).\n\n"
                "After that, I'll confirm my understanding and continue the interview."
            )
            cl.user_session.set("awaiting_company_description", True)
            cl.user_session.set("post_company_confirmation_prompt", interview_start_prompt)

        messages = cl.user_session.get("messages") or []
        messages.append({"role": "assistant", "content": greeting})
        cl.user_session.set("messages", messages)
        await send_assistant_message(greeting)
        save_checkpoint(message)
    finally:
        if int(cl.user_session.get("company_setup_token") or 0) == setup_token:
            cl.user_session.set("company_setup_in_progress", False)


async def handle_collection_step(collection_step: str, user_input: str, save_checkpoint, message: cl.Message = None) -> bool:
    metadata = cl.user_session.get("metadata")
    messages = cl.user_session.get("messages") or []

    if collection_step == "name":
        parsed = parse_collection_response("name", user_input, messages)
        metadata["employee_name"] = metadata_value_from_intent("name", parsed)
        cl.user_session.set("metadata", metadata)
        cl.user_session.set("collection_step", "company")
        next_prompt = "**What company do you work for?**"
        messages.append({"role": "user", "content": user_input or "anonymous"})
        messages.append({"role": "assistant", "content": next_prompt})
        cl.user_session.set("messages", messages)
        await send_assistant_message(next_prompt)
        save_checkpoint(message)
        return True

    if collection_step == "company":
        parsed = parse_collection_response("company", user_input, messages)
        metadata["company"] = metadata_value_from_intent("company", parsed)
        cl.user_session.set("metadata", metadata)
        cl.user_session.set("company_context_confirmed", False)
        next_step = "company_website" if metadata.get("company") else "email"
        cl.user_session.set("collection_step", next_step)
        next_prompt = collection_prompt_for_step(next_step)
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": next_prompt})
        cl.user_session.set("messages", messages)
        await send_assistant_message(next_prompt)
        save_checkpoint(message)
        return True

    if collection_step == "company_website":
        if cl.user_session.get("interview_started"):
            cl.user_session.set("collection_step", None)
            save_checkpoint(message)
            return True
        if cl.user_session.get("company_setup_in_progress"):
            save_checkpoint(message)
            return True

        parsed = parse_collection_response("company_website", user_input, messages)
        metadata["company_website"] = metadata_value_from_intent("company_website", parsed)
        cl.user_session.set("metadata", metadata)
        cl.user_session.set("company_context_confirmed", False)
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)
        cl.user_session.set("collection_step", None)
        await run_company_setup(save_checkpoint, message)
        return True

    if collection_step == "email":
        parsed = parse_collection_response("email", user_input, messages)
        if parsed.get("intent") == "invalid":
            await send_assistant_message("Please provide a valid work email address.")
            save_checkpoint(message)
            return True
        metadata["email"] = metadata_value_from_intent("email", parsed)
        cl.user_session.set("metadata", metadata)
        cl.user_session.set("collection_step", "department")
        next_prompt = "**What department do you work in?**"
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": next_prompt})
        cl.user_session.set("messages", messages)
        await send_assistant_message(next_prompt)
        save_checkpoint(message)
        return True

    if collection_step == "department":
        parsed = parse_collection_response("department", user_input, messages)
        metadata["department"] = metadata_value_from_intent("department", parsed)
        cl.user_session.set("metadata", metadata)
        cl.user_session.set("collection_step", "role")
        next_prompt = "**What's your position/role?**"
        messages.append({"role": "user", "content": user_input})
        messages.append({"role": "assistant", "content": next_prompt})
        cl.user_session.set("messages", messages)
        await send_assistant_message(next_prompt)
        save_checkpoint(message)
        return True

    if collection_step == "role":
        parsed = parse_collection_response("role", user_input, messages)
        metadata["role"] = metadata_value_from_intent("role", parsed)
        cl.user_session.set("metadata", metadata)
        cl.user_session.set("collection_step", None)
        messages.append({"role": "user", "content": user_input})
        cl.user_session.set("messages", messages)
        if metadata.get("company") and cl.user_session.get("company_context_confirmed", False):
            await start_interview_with_company_context(save_checkpoint, message)
        else:
            await run_company_setup(save_checkpoint, message)
        return True

    return False
