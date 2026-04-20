import re


def is_answer_too_short(text: str) -> bool:
    """Check if user answer is too short/vague."""
    words = text.strip().split()
    return len(words) < 3


def _as_list(value):
    return value if isinstance(value, list) else []


def _looks_like_interview_start(content: str) -> bool:
    text = str(content or "").strip().lower()
    return (
        "**let's start:**" in text
        or "**what are the main business goals or strategic priorities for your organization?**" in text
    )


def _find_interview_start_index(messages: list) -> int:
    for idx, message in enumerate(messages or []):
        if str(message.get("role", "")).strip().lower() != "assistant":
            continue
        if _looks_like_interview_start(message.get("content", "")):
            return idx
    return 0


def count_user_turns(messages: list) -> int:
    start_index = _find_interview_start_index(messages)
    return sum(
        1
        for idx, message in enumerate(messages or [])
        if idx > start_index and message.get("role") == "user"
    )


def looks_like_finish_request(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    patterns = [
        r"\bfinish up\b",
        r"\bfinish (the )?interview\b",
        r"\bend (the )?interview\b",
        r"\bwrap up\b",
        r"\blet'?s stop here\b",
        r"\bstop here\b",
        r"\bmove to the final review\b",
        r"\bgo to the final review\b",
        r"\bclose the interview\b",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def evaluate_notes_readiness(notes: dict, seniority_level: str) -> dict:
    tasks = _as_list(notes.get("tasks"))
    business_goals = _as_list(notes.get("business_goals"))
    kpis = _as_list(notes.get("kpis_mentioned"))
    data_sources = _as_list(notes.get("data_sources"))
    data_quality_comments = _as_list(notes.get("data_quality_comments"))
    regulatory_concerns = _as_list(notes.get("regulatory_concerns"))
    technical_constraints = _as_list(notes.get("technical_constraints"))

    task_described_count = sum(
        1 for t in tasks
        if isinstance(t, dict) and str(t.get("description", "")).strip()
    )
    task_with_friction_count = sum(
        1 for t in tasks
        if isinstance(t, dict) and str(t.get("friction_level", "")).strip()
    )
    friction_points_count = sum(
        len(_as_list(t.get("friction_points"))) for t in tasks if isinstance(t, dict)
    )
    task_systems_count = sum(
        len(_as_list(t.get("current_systems"))) for t in tasks if isinstance(t, dict)
    )

    goals_or_kpis_count = len(business_goals) + len(kpis)
    systems_or_data_count = len(data_sources) + task_systems_count
    feasibility_signal_count = (
        len(data_quality_comments) +
        len(regulatory_concerns) +
        len(technical_constraints)
    )
    thresholds = {"tasks": 3, "friction_points": 2, "goals_kpis": 2, "factors": 1, "systems_data": 1}

    strict_ready = (
        task_described_count >= thresholds["tasks"] and
        task_with_friction_count >= max(1, thresholds["tasks"] - 1) and
        friction_points_count >= thresholds["friction_points"] and
        goals_or_kpis_count >= thresholds["goals_kpis"] and
        systems_or_data_count >= thresholds["systems_data"] and
        feasibility_signal_count >= thresholds["factors"]
    )

    turn_limit_ready = (
        task_described_count >= 2 and
        friction_points_count >= 1 and
        goals_or_kpis_count >= 1 and
        (systems_or_data_count >= 1 or feasibility_signal_count >= 1)
    )

    return {
        "strict_ready": strict_ready,
        "turn_limit_ready": turn_limit_ready,
        "task_described_count": task_described_count,
        "task_with_friction_count": task_with_friction_count,
        "friction_points_count": friction_points_count,
        "goals_or_kpis_count": goals_or_kpis_count,
        "systems_or_data_count": systems_or_data_count,
        "feasibility_signal_count": feasibility_signal_count,
        "llm_ready_for_report": bool(notes.get("ready_for_report")),
    }
