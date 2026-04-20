import json

from ai_client import MODEL, get_client


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
        return "No problem. Even roughly, could you walk me through that step by step?"

    return response


def paraphrase_repeated_question(response: str, messages: list, fallback: str = "") -> str:
    """Paraphrase a repeated assistant question so loops sound less robotic."""
    if not isinstance(response, str) or not response.strip():
        return fallback or response

    assistant_messages = [
        str(m.get("content", "")).strip()
        for m in (messages or [])
        if isinstance(m, dict) and m.get("role") == "assistant" and str(m.get("content", "")).strip()
    ]
    if not assistant_messages:
        return response

    response_norm = response.strip().lower()
    if response_norm not in {msg.lower() for msg in assistant_messages[-3:]}:
        return response

    payload = {
        "question": response,
        "recent_assistant_messages": assistant_messages[-4:],
    }
    system_prompt = """You paraphrase an interview question so it does not sound repeated.

Rules:
- Preserve the meaning.
- Keep it concise and natural.
- Do not add new requirements or new questions.
- Return one short paraphrased prompt only.
"""

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        if content and content.lower() != response_norm:
            return content
    except Exception:
        pass

    return fallback or response


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
    if metadata.get("interview_focus"):
        meta_lines.append(f"interview_focus: {metadata.get('interview_focus', '')}")
    excluded_topics = metadata.get("out_of_scope_topics") or []
    if excluded_topics:
        meta_lines.append(f"out_of_scope_topics: {json.dumps(excluded_topics, ensure_ascii=True)}")
    term_contexts = metadata.get("term_contexts") or []
    if term_contexts:
        meta_lines.append("term_contexts:")
        for item in term_contexts:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term", "")).strip()
            public_context = str(item.get("public_context", "")).strip()
            user_explanation = str(item.get("user_explanation", "")).strip()
            if term:
                meta_lines.append(f"- term: {term}")
                if public_context:
                    meta_lines.append(f"  public_context: {public_context}")
                if user_explanation:
                    meta_lines.append(f"  user_explanation: {user_explanation}")
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
