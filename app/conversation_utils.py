def get_interview_strategy_description(seniority: str) -> str:
    """Get description of what this seniority level contributes."""
    descriptions = {
        "executive": "understand strategic priorities and organizational readiness",
        "senior": "capture department-level processes and cross-functional opportunities",
        "intermediate": "document detailed workflows and practical implementation concerns",
        "junior": "identify hands-on task friction and day-to-day bottlenecks",
        "intern": "discover entry-level task challenges and tool usability issues",
    }
    return descriptions.get(seniority, "gather valuable insights")


def has_valid_north_star(value) -> bool:
    """Treat legacy seeded company-info strings as not-a-real North Star."""
    if not value or not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return False
    lowered = v.lower()
    return not (
        lowered.startswith("company info:")
        or lowered.startswith("company info (user-provided):")
    )


def avoid_immediate_question_repeat(response: str, messages: list) -> str:
    """Avoid asking the exact same assistant question twice in a row."""
    if not isinstance(response, str):
        return "Could you tell me more about your process?"

    if not response or not messages:
        return response

    last_assistant = None
    for m in reversed(messages):
        if m.get("role") == "assistant":
            last_assistant = m.get("content", "")
            break

    if not last_assistant:
        return response

    if not isinstance(last_assistant, str):
        return response

    if response.strip().lower() == last_assistant.strip().lower():
        return (
            "No problem. Even roughly, what happens right before the call, during the call, "
            "and right after the call with a potential provider?"
        )

    return response


def normalize_framework_step(step: str) -> str:
    """Map internal step keys to generic question categories."""
    mapping = {
        "step_1_north_star": "north_star",
        "step_2_tasks": "tasks",
    }
    return mapping.get(step, step or "")


def build_analysis_transcript(messages: list, metadata: dict) -> str:
    """
    Build transcript sent to LLMs.
    Includes known metadata so role/department/company are always in context.
    """
    meta_lines = [
        "interview_metadata:",
        f"employee_name: {metadata.get('employee_name', 'Anonymous')}",
        f"email: {metadata.get('email', '')}",
        f"department: {metadata.get('department', '')}",
        f"role: {metadata.get('role', '')}",
        f"company: {metadata.get('company', '')}",
        f"seniority_level: {metadata.get('seniority_level', '')}",
        f"north_star_source_hint: {metadata.get('north_star_source_hint', 'not_specified')}",
    ]
    chat_lines = [f'{m["role"]}: {m["content"]}' for m in messages]
    return "\n".join(meta_lines + ["", "chat_transcript:"] + chat_lines)


def thread_is_completed(thread: dict) -> bool:
    """Best-effort detection for already-completed interview threads."""
    try:
        steps = thread.get("steps", []) if isinstance(thread, dict) else []
        for step in steps:
            output = step.get("output", "") if isinstance(step, dict) else ""
            if isinstance(output, str) and "This interview is now complete." in output:
                return True
    except Exception:
        pass
    return False
