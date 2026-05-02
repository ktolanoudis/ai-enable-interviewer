import datetime
import hashlib
import json
import os
import re
import traceback
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import chainlit as cl

from company_memory import assess_theme_alignment, extract_company_recurring_themes
from conversation_utils import build_analysis_transcript
from db import delete_interview_checkpoint, delete_interview_checkpoints_for_session, save_session, update_company_insights
from report_agent import generate_report
from report_formatting import generate_markdown_report
from session_state import POST_INTERVIEW_SURVEY_TEXT, POST_INTERVIEW_SURVEY_URL
from storage import persist_report_files


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


MAX_USE_CASE_FEEDBACK_ITEMS = _int_env("MAX_USE_CASE_FEEDBACK_ITEMS", 5)


def is_existing_capability_feedback(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower().replace("’", "'"))
    if not normalized:
        return False
    markers = (
        "i already do this",
        "we already do this",
        "already do this",
        "i already use",
        "we already use",
        "already use",
        "this already exists",
        "already exists",
        "we have this",
        "we already have this",
        "already implemented",
        "currently implemented",
        "already in place",
        "company is implementing",
        "we are implementing",
        "we're implementing",
        "being implemented",
        "existing tool",
        "existing system",
        "current tool does this",
    )
    return any(marker in normalized for marker in markers)


def use_case_feedback_status_from_comment(text: str) -> str:
    return "existing_capability" if is_existing_capability_feedback(text) else "new_opportunity_feedback"


def serialize_report_payload(report, use_case_feedback: list) -> str:
    payload = {"report": report.model_dump(), "employee_use_case_feedback": use_case_feedback or []}
    return json.dumps(payload, indent=2)


def append_use_case_feedback_markdown(md_content: str, use_case_feedback: list) -> str:
    if not use_case_feedback:
        return md_content
    lines = [md_content.rstrip(), "", "---", "", "## Employee Feedback On Suggested AI Use Cases", ""]
    for idx, item in enumerate(use_case_feedback, 1):
        name = str(item.get("use_case_name", "AI Use Case")).strip() or "AI Use Case"
        rating = item.get("rating")
        reason = str(item.get("comment", "")).strip()
        status = str(item.get("status", "") or "").strip()
        feasibility = item.get("feasibility_feedback") or {}
        lines.append(f"### Feedback {idx}: {name}")
        if status == "existing_capability":
            lines.append("- **Status:** Existing capability / already in use")
            lines.append(f"- **Current Solution Rating:** {rating}/5" if rating is not None else "- **Current Solution Rating:** Skipped")
        else:
            lines.append(f"- **Rating:** {rating}/5" if rating is not None else "- **Rating:** Skipped")
        lines.append(f"- **Comment:** {reason}" if reason else "- **Comment:** No additional comment provided.")
        if feasibility:
            lines.append("- **Feasibility Review:**")
            dq = str(feasibility.get("data_quality_comment", "")).strip()
            rr = str(feasibility.get("regulatory_comment", "")).strip()
            ex = str(feasibility.get("explainability_comment", "")).strip()
            if dq:
                lines.append(f"  - Data quality / availability: {dq}")
            if rr:
                lines.append(f"  - Regulatory / compliance: {rr}")
            if ex:
                lines.append(f"  - Explainability: {ex}")
            if not any([dq, rr, ex]):
                lines.append("  - No additional feasibility comment provided.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_use_case_rating(user_input: str):
    text = str(user_input or "").strip().lower()
    if text in {"skip", "pass", "n/a", "na"}:
        return "skip"
    exact = re.fullmatch(r"([1-5])(?:\s*/\s*5)?", text)
    if exact:
        return int(exact.group(1))
    match = re.search(r"\b([1-5])\b", text)
    if match:
        return int(match.group(1))
    return None


def build_use_case_rating_prompt(use_case: dict, index: int, total: int, include_intro: bool = False) -> str:
    name = str(use_case.get("use_case_name", "AI Use Case")).strip() or "AI Use Case"
    description = str(use_case.get("description", "")).strip()
    impact = str(use_case.get("expected_impact", "")).strip()
    parts = []
    if include_intro:
        parts.extend(
            [
                "Next, we will review the suggested AI use cases one by one.",
                "For each one, give a brief reaction in your own words first. Then I will ask you to rate it from 1 to 5.",
                "",
            ]
        )
    parts.append(f"## {name} ({index}/{total})")
    if description:
        parts.extend(["", description])
    if impact and impact.lower() != "not specified":
        parts.extend(["", f"Expected impact: {impact}"])
    parts.extend(["", "How does this seem for your work in practice?", "You can answer briefly in your own words. If you want to skip this comment, just type 'skip'."])
    return "\n".join(parts)


def build_use_case_rating_followup(feedback_status: str = "") -> str:
    if feedback_status == "existing_capability":
        return (
            "Since this already exists or is already being done, how well does the current solution work from 1 to 5, "
            "where 1 means it works poorly and 5 means it works very well? You can also type 'skip'."
        )
    return "How would you rate it from 1 to 5, where 1 means not useful and 5 means very useful? You can also type 'skip'."


def build_use_case_rating_actions() -> list:
    labels = [
        ("1", "1"),
        ("2", "2"),
        ("3", "3"),
        ("4", "4"),
        ("5", "5"),
        ("skip", "Skip"),
    ]
    return [
        cl.Action(
            name="use_case_rating",
            payload={"rating": value},
            label=label,
        )
        for value, label in labels
    ]


def build_use_case_feasibility_rating_actions(dimension: str) -> list:
    dimension = str(dimension or "").strip().lower()
    if dimension == "regulatory_risk":
        labels = [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
            ("skip", "Skip"),
        ]
    else:
        labels = [
            ("1", "1"),
            ("2", "2"),
            ("3", "3"),
            ("4", "4"),
            ("5", "5"),
            ("skip", "Skip"),
        ]
    return [
        cl.Action(
            name="feasibility_rating",
            payload={"dimension": dimension, "rating": value},
            label=label,
        )
        for value, label in labels
    ]


def build_use_case_feasibility_rating_followup(dimension: str) -> str:
    dimension = str(dimension or "").strip().lower()
    if dimension == "data_quality":
        return "How would you rate data readiness from 1 to 5, where 1 means not ready and 5 means very ready? You can also skip."
    if dimension == "explainability":
        return "How important is explainability from 1 to 5, where 1 means not important and 5 means very important? You can also skip."
    if dimension == "regulatory_risk":
        return "How would you rate the regulatory or compliance risk: low, medium, high, or critical? You can also skip."
    return "How would you rate this feasibility point? You can also skip."


def build_use_case_feasibility_prompt(dimension: str, is_first: bool = False, variant: int = 0) -> str:
    intros = [
        "One short feasibility check before we move on.",
        "One quick feasibility question before the next use case.",
        "Before the next one, I want to check one practical feasibility point.",
    ]
    closers = [
        "A brief answer is enough. If this part is outside your visibility, just say so.",
        "A short practical answer is enough. If you do not really see this part from your role, just say that.",
        "You do not need to be exhaustive here. If this is outside your visibility, just say so.",
    ]
    prompts = {
        "data_quality": [
            (
                "Based on what you know in your role, how does this look in terms of data quality and data availability?\n"
                "For example, does the needed data exist, look reliable enough, and seem accessible in practice?"
            ),
            (
                "From your perspective, is the data for something like this actually there and usable?\n"
                "I mean both whether the needed information exists and whether it is clean or consistent enough to rely on."
            ),
            (
                "How solid does the data foundation for this seem in practice?\n"
                "For example, would the right inputs be available, trustworthy enough, and reachable without too much friction?"
            ),
        ],
        "regulatory_risk": [
            (
                "Based on what you know in your role, how does this look in terms of regulatory or compliance risk?\n"
                "For example, do you see privacy, legal, policy, or approval concerns here? Please call it low, medium, high, or critical if you can."
            ),
            (
                "From what you can see, would something like this raise any privacy, legal, policy, or approval concerns?\n"
                "I am mainly asking whether there are compliance constraints that could make it harder to use safely. A low, medium, high, or critical risk label is useful if you know."
            ),
            (
                "How risky does this seem from a regulatory or compliance point of view?\n"
                "For example, do you think there would be privacy, legal, or internal policy concerns to work through? Please estimate low, medium, high, or critical if possible."
            ),
        ],
        "explainability": [
            (
                "Based on what you know in your role, how important would explainability be for this use case?\n"
                "For example, would the AI output need to be easy to justify, audit, or explain to others?"
            ),
            (
                "How important would it be for this kind of AI output to be easy to explain or justify?\n"
                "For example, would people need to understand why it suggested something before trusting or using it?"
            ),
            (
                "From your side, would this need to be highly explainable to work in practice?\n"
                "I mean whether the output would need to be easy to justify, audit, or defend to others."
            ),
        ],
    }
    prompt_variants = prompts.get(dimension) or []
    if not prompt_variants:
        return ""
    prompt_variant = prompt_variants[variant % len(prompt_variants)]
    opener = intros[variant % len(intros)] + "\n\n" if is_first else ""
    closer = closers[variant % len(closers)]
    return opener + prompt_variant + "\n" + closer


def build_company_contributor(metadata: dict) -> dict:
    email = str(metadata.get("email", "") or "").strip().lower()
    owner_fingerprint = str(cl.user_session.get("owner_fingerprint", "") or "").strip()
    employee_name = str(metadata.get("employee_name", "") or "").strip()
    role = str(metadata.get("role", "") or "").strip()
    department = str(metadata.get("department", "") or "").strip()

    source = ""
    if email:
        source = f"email:{email}"
    elif owner_fingerprint:
        source = f"owner:{owner_fingerprint}"
    elif employee_name and employee_name.lower() != "anonymous":
        source = f"name:{employee_name.lower()}|role:{role.lower()}|department:{department.lower()}"

    contributor_key = ""
    if source:
        contributor_key = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]

    return {
        "contributor_key": contributor_key,
        "employee": employee_name or "Anonymous",
        "role": role,
        "department": department,
    }


def build_post_interview_survey_url(base_url: str, contributor_key: str) -> str:
    base_url = str(base_url or "").strip()
    contributor_key = str(contributor_key or "").strip()
    if not base_url or not contributor_key:
        return base_url

    parts = urlsplit(base_url)
    query_items = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "r"]
    query_items.append(("r", contributor_key))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_items), parts.fragment))


def build_validated_use_case_entries(feedback_entries: list, metadata: dict, contributor: dict | None = None) -> list:
    grouped = {}
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()
    employee_name = metadata.get("employee_name", "Anonymous")
    role = metadata.get("role", "")
    department = metadata.get("department", "")
    contributor_key = str((contributor or {}).get("contributor_key", "") or "").strip()
    for item in feedback_entries or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("use_case_name", "")).strip()
        if not name:
            continue
        task_name = str(item.get("task_name", "") or "").strip().lower()
        ai_solution_type = str(item.get("ai_solution_type", "") or "").strip().lower()
        if task_name and ai_solution_type:
            key = f"{task_name}::{ai_solution_type}"
        elif task_name:
            key = task_name
        else:
            key = " ".join(name.lower().split())
        rating = item.get("rating")
        comment = str(item.get("comment", "")).strip()
        group = grouped.setdefault(
            key,
            {
                "use_case_name": name,
                "latest_description": str(item.get("description", "")).strip(),
                "task_name": str(item.get("task_name", "")).strip(),
                "ai_solution_type": str(item.get("ai_solution_type", "")).strip(),
                "rating_count": 0,
                "rating_sum": 0.0,
                "average_rating": None,
                "existing_capability_count": 0,
                "existing_solution_rating_count": 0,
                "existing_solution_rating_sum": 0.0,
                "average_existing_solution_rating": None,
                "support_count": 0,
                "concern_count": 0,
                "data_quality_score_count": 0,
                "data_quality_score_sum": 0.0,
                "average_data_quality_score": None,
                "explainability_score_count": 0,
                "explainability_score_sum": 0.0,
                "average_explainability_score": None,
                "regulatory_risk_counts": {
                    "low": 0,
                    "medium": 0,
                    "high": 0,
                    "critical": 0,
                    "unknown": 0,
                },
                "safe_to_pursue_counts": {
                    "yes": 0,
                    "no": 0,
                    "unclear": 0,
                },
                "contributor_keys": [contributor_key] if contributor_key else [],
                "comments": [],
                "last_updated": timestamp,
            },
        )
        status = str(item.get("status", "") or "").strip().lower()
        if isinstance(rating, int):
            if status == "existing_capability":
                group["existing_solution_rating_count"] += 1
                group["existing_solution_rating_sum"] += rating
                group["average_existing_solution_rating"] = round(
                    group["existing_solution_rating_sum"] / group["existing_solution_rating_count"],
                    2,
                )
            else:
                group["rating_count"] += 1
                group["rating_sum"] += rating
                group["average_rating"] = round(group["rating_sum"] / group["rating_count"], 2)
                if rating >= 4:
                    group["support_count"] += 1
                elif rating <= 2:
                    group["concern_count"] += 1
        if status == "existing_capability":
            group["existing_capability_count"] += 1
        feasibility = item.get("feasibility_feedback") or {}
        data_quality_score = feasibility.get("data_quality_score")
        if isinstance(data_quality_score, int) and 1 <= data_quality_score <= 5:
            group["data_quality_score_count"] += 1
            group["data_quality_score_sum"] += data_quality_score
            group["average_data_quality_score"] = round(
                group["data_quality_score_sum"] / group["data_quality_score_count"],
                2,
            )
        explainability_score = feasibility.get("explainability_score")
        if isinstance(explainability_score, int) and 1 <= explainability_score <= 5:
            group["explainability_score_count"] += 1
            group["explainability_score_sum"] += explainability_score
            group["average_explainability_score"] = round(
                group["explainability_score_sum"] / group["explainability_score_count"],
                2,
            )
        risk = str(feasibility.get("regulatory_risk", "") or "").strip().lower()
        if risk:
            group["regulatory_risk_counts"][risk if risk in group["regulatory_risk_counts"] else "unknown"] += 1
        safe_to_pursue = str(feasibility.get("safe_to_pursue", "") or "").strip().lower()
        if safe_to_pursue:
            group["safe_to_pursue_counts"][safe_to_pursue if safe_to_pursue in group["safe_to_pursue_counts"] else "unclear"] += 1
        if comment or any(feasibility.values()):
            group["comments"].append(
                {
                    "employee": employee_name,
                    "role": role,
                    "department": department,
                    "rating": rating,
                    "status": status,
                    "comment": comment,
                    "contributor_key": contributor_key,
                    "feasibility_feedback": feasibility,
                    "created_at": timestamp,
                }
            )
    return list(grouped.values())


def _normalize_tokens(*values) -> set[str]:
    tokens = set()
    for value in values:
        tokens.update(re.findall(r"[a-z0-9]+", str(value or "").lower()))
    return {token for token in tokens if len(token) > 2}


def _feedback_relevance_score(use_case: dict, report_payload: dict, metadata: dict) -> int:
    role_tokens = _normalize_tokens(metadata.get("role"), metadata.get("department"))
    use_case_tokens = _normalize_tokens(
        use_case.get("task_name"),
        use_case.get("use_case_name"),
        use_case.get("description"),
        use_case.get("ai_solution_type"),
    )
    score = len(role_tokens & use_case_tokens)

    task_name = " ".join(str(use_case.get("task_name", "")).lower().split())
    for task in report_payload.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        candidate_name = " ".join(str(task.get("name", "")).lower().split())
        if task_name and candidate_name and (task_name == candidate_name or task_name in candidate_name or candidate_name in task_name):
            score += 4
        task_department = str(task.get("department", "") or "").strip().lower()
        metadata_department = str(metadata.get("department", "") or "").strip().lower()
        if task_department and metadata_department and task_department == metadata_department:
            score += 2
        if task.get("friction_points"):
            score += 1

    if str(use_case.get("expected_impact", "") or "").strip().lower() not in {"", "not specified"}:
        score += 1
    return score


def rank_feedback_use_cases(report_payload: dict, metadata: dict) -> list[dict]:
    use_cases = [uc for uc in (report_payload.get("use_cases") or []) if isinstance(uc, dict)]
    if not use_cases:
        return []
    ranked = sorted(
        enumerate(use_cases),
        key=lambda item: (-_feedback_relevance_score(item[1], report_payload, metadata), item[0]),
    )
    limit = max(1, MAX_USE_CASE_FEEDBACK_ITEMS)
    return [item for _idx, item in ranked[:limit]]


def ensure_report_payload(messages: list, metadata: dict) -> dict:
    payload = cl.user_session.get("pending_report_payload")
    if isinstance(payload, dict) and payload.get("use_cases") is not None:
        return payload
    analysis_transcript = build_analysis_transcript(messages, metadata)
    report = generate_report(analysis_transcript)
    payload = report.model_dump()
    cl.user_session.set("pending_report_payload", payload)
    return payload


async def begin_use_case_feedback(send_assistant_message, messages: list, metadata: dict):
    try:
        report_payload = ensure_report_payload(messages, metadata)
    except Exception:
        traceback.print_exc()
        return None
    use_cases = rank_feedback_use_cases(report_payload, metadata)
    if not use_cases:
        return None
    report_payload = dict(report_payload)
    report_payload["use_cases"] = use_cases
    cl.user_session.set("pending_report_payload", report_payload)
    cl.user_session.set("awaiting_use_case_feedback_consent", False)
    cl.user_session.set("awaiting_use_case_opinion", False)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("awaiting_use_case_feasibility", False)
    cl.user_session.set("use_case_feedback_index", 0)
    cl.user_session.set("use_case_feedback_entries", [])
    cl.user_session.set("current_use_case_feedback", None)
    cl.user_session.set("current_use_case_feasibility_scope", None)
    return await send_next_use_case_feedback_prompt(send_assistant_message, messages)


async def send_next_use_case_feedback_prompt(send_assistant_message, messages: list):
    report_payload = cl.user_session.get("pending_report_payload") or {}
    use_cases = report_payload.get("use_cases") or []
    index = int(cl.user_session.get("use_case_feedback_index", 0) or 0)
    if index >= len(use_cases):
        return None
    prompt = build_use_case_rating_prompt(
        use_cases[index],
        index + 1,
        len(use_cases),
        include_intro=index == 0,
    )
    cl.user_session.set("awaiting_use_case_opinion", True)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("awaiting_use_case_feasibility", False)
    messages.append({"role": "assistant", "content": prompt})
    cl.user_session.set("messages", messages)
    await send_assistant_message(prompt)
    return prompt


async def close_interview(send_assistant_message, messages: list, transcript: str, analysis_transcript: str, seniority_level: str, interview_count: int, report_payload: dict = None, use_case_feedback: list = None):
    cl.user_session.set("awaiting_final_confirmation", False)
    cl.user_session.set("awaiting_final_addendum", False)
    cl.user_session.set("finalization_failed", False)

    if POST_INTERVIEW_SURVEY_URL:
        closing_msg = "Thank you for your time. I’m finalizing your report now. One final required step will appear next."
    else:
        closing_msg = "Thank you for your time. I’m finalizing your report now."
    messages.append({"role": "assistant", "content": closing_msg})
    cl.user_session.set("messages", messages)
    await send_assistant_message(closing_msg)

    try:
        if report_payload:
            try:
                from .schemas import Report
            except ImportError:
                from schemas import Report
            report = Report(**report_payload)
        else:
            report = generate_report(analysis_transcript)

        session_id = cl.user_session.get("session_id")
        metadata = cl.user_session.get("metadata") or {}
        contributor = build_company_contributor(metadata)
        md_content = generate_markdown_report(report, metadata)
        md_content = append_use_case_feedback_markdown(md_content, use_case_feedback or [])
        report_json = serialize_report_payload(report, use_case_feedback or [])
        persist_report_files(session_id, report_json, md_content)
        company_name = metadata.get("company") or "Unknown company"
        save_session(
            company=company_name,
            employee=metadata.get("employee_name", "Anonymous"),
            department=metadata.get("department", ""),
            role=metadata.get("role", ""),
            seniority_level=seniority_level,
            transcript=transcript,
            report_json=report_json,
            report_md=md_content,
            contributor=contributor,
        )
        north_star = (
            report.north_star_alignment
            if report.north_star_alignment and report.north_star_source == "senior_stakeholder_interview"
            else None
        )
        company_context = cl.user_session.get("company_context") or {}
        recurring_themes = extract_company_recurring_themes(
            analysis_transcript,
            metadata,
            cl.user_session.get("notes") or {},
        )
        theme_alignments = assess_theme_alignment(
            analysis_transcript,
            metadata,
            cl.user_session.get("notes") or {},
            company_context.get("recurring_themes", []),
        )
        contradiction_updates = []
        for item in theme_alignments:
            if str(item.get("stance", "")).strip().lower() != "contradict":
                continue
            contradiction_updates.append(
                {
                    "theme_key": item.get("theme_key"),
                    "mention_count": 0,
                    "contradiction_count": 1,
                    "contradiction_evidence": item.get("evidence", ""),
                }
            )
        if metadata.get("company"):
            update_company_insights(
                company=company_name,
                north_star=north_star,
                department=metadata.get("department", ""),
                tasks=[t.model_dump() for t in report.tasks],
                use_cases=[uc.model_dump() for uc in report.use_cases],
                validated_use_cases=build_validated_use_case_entries(use_case_feedback or [], metadata, contributor=contributor),
                recurring_themes=(recurring_themes or []) + contradiction_updates,
                contributor=contributor,
            )

        download_elements = [
            cl.File(
                name=f"report_{session_id}.md",
                content=md_content,
                display="inline",
                mime="text/markdown",
            ),
            cl.File(
                name=f"report_{session_id}.json",
                content=report_json,
                display="inline",
                mime="application/json",
            ),
        ]

        if download_elements:
            download_msg = cl.Message(
                content="Your report is ready. You can download it below.",
                author="Interviewer",
                elements=download_elements,
            )
            await download_msg.send()

        completion_msg = "This interview is now complete. Your answers will be taken into consideration."
        messages.append({"role": "assistant", "content": completion_msg})
        cl.user_session.set("messages", messages)
        await send_assistant_message(completion_msg)

        if POST_INTERVIEW_SURVEY_URL:
            survey_url = build_post_interview_survey_url(
                POST_INTERVIEW_SURVEY_URL,
                str(contributor.get("contributor_key", "") or ""),
            )
            survey_msg = cl.Message(
                content=(
                    f"{POST_INTERVIEW_SURVEY_TEXT}\n\n"
                    f"[Continue to the experience survey in a new tab]({survey_url})"
                ),
                author="Interviewer",
            )
            await survey_msg.send()
        cl.user_session.set("report_done", True)
        cl.user_session.set("collection_step", "__closed__")
        try:
            draft_id = cl.user_session.get("active_draft_id")
            if draft_id:
                delete_interview_checkpoint(str(draft_id))
            if session_id:
                delete_interview_checkpoints_for_session(
                    str(session_id),
                    owner_fingerprint=str(cl.user_session.get("owner_fingerprint", "") or "").strip(),
                )
        except Exception:
            traceback.print_exc()
    except Exception:
        traceback.print_exc()
        error_msg = (
            "I couldn't finalize the report because of a processing or storage error. "
            "Your draft is still saved; type 'finish interview' to retry finalization."
        )
        messages.append({"role": "assistant", "content": error_msg})
        cl.user_session.set("messages", messages)
        cl.user_session.set("finalization_failed", True)
        await send_assistant_message(error_msg)
        return
    finally:
        if cl.user_session.get("report_done"):
            cl.user_session.set("pending_report_payload", None)
            cl.user_session.set("awaiting_use_case_feedback_consent", False)
            cl.user_session.set("awaiting_use_case_opinion", False)
            cl.user_session.set("awaiting_use_case_rating", False)
            cl.user_session.set("awaiting_use_case_feasibility", False)
            cl.user_session.set("use_case_feedback_index", 0)
            cl.user_session.set("use_case_feedback_entries", [])
            cl.user_session.set("current_use_case_feedback", None)
            cl.user_session.set("current_use_case_feasibility_scope", None)
