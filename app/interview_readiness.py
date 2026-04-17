def is_answer_too_short(text: str) -> bool:
    """Check if user answer is too short/vague."""
    words = text.strip().split()
    return len(words) < 3


def _as_list(value):
    return value if isinstance(value, list) else []


def count_user_turns(messages: list) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


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
    has_role_department = bool(notes.get("role")) and bool(notes.get("department"))

    thresholds = {"tasks": 3, "friction_points": 2, "goals_kpis": 2, "factors": 1, "systems_data": 1}

    strict_ready = (
        has_role_department and
        task_described_count >= thresholds["tasks"] and
        task_with_friction_count >= max(1, thresholds["tasks"] - 1) and
        friction_points_count >= thresholds["friction_points"] and
        goals_or_kpis_count >= thresholds["goals_kpis"] and
        systems_or_data_count >= thresholds["systems_data"] and
        feasibility_signal_count >= thresholds["factors"]
    )

    turn_limit_ready = (
        has_role_department and
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
