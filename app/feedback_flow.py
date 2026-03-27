import datetime
import json
import re
import traceback

import chainlit as cl

from conversation_utils import build_analysis_transcript
from db import delete_interview_checkpoint, save_session, update_company_insights
from report_agent import generate_report
from report_formatting import generate_markdown_report
from storage import persist_report_files


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
        feasibility = item.get("feasibility_feedback") or {}
        lines.append(f"### Feedback {idx}: {name}")
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


def build_use_case_feedback_invitation(use_cases: list) -> str:
    count = len(use_cases or [])
    plural = "use cases" if count != 1 else "use case"
    return (
        f"I came up with {count} specific AI {plural} that could reduce repetitive work in your day, "
        "and I would like your feedback on them before the interview is complete.\n\n"
        "For each one, I'll ask you to rate it from 1 to 5 and tell me briefly why it would or would not help. "
        "This should only take a couple of minutes.\n\n"
        "Would you like to review them now? (yes/no)"
    )


def build_use_case_rating_prompt(use_case: dict, index: int, total: int) -> str:
    name = str(use_case.get("use_case_name", "AI Use Case")).strip() or "AI Use Case"
    description = str(use_case.get("description", "")).strip()
    impact = str(use_case.get("expected_impact", "")).strip()
    parts = [f"## {name} ({index}/{total})"]
    if description:
        parts.extend(["", description])
    if impact and impact.lower() != "not specified":
        parts.extend(["", f"Expected impact: {impact}"])
    parts.extend(["", "How does this seem for your work in practice?", "You can answer briefly in your own words. If you want to skip this comment, just type 'skip'."])
    return "\n".join(parts)


def build_use_case_rating_followup() -> str:
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
                "For example, do you see privacy, legal, policy, or approval concerns here?"
            ),
            (
                "From what you can see, would something like this raise any privacy, legal, policy, or approval concerns?\n"
                "I am mainly asking whether there are compliance constraints that could make it harder to use safely."
            ),
            (
                "How risky does this seem from a regulatory or compliance point of view?\n"
                "For example, do you think there would be privacy, legal, or internal policy concerns to work through?"
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


def build_validated_use_case_entries(feedback_entries: list, metadata: dict) -> list:
    grouped = {}
    timestamp = datetime.datetime.utcnow().isoformat()
    employee_name = metadata.get("employee_name", "Anonymous")
    role = metadata.get("role", "")
    department = metadata.get("department", "")
    for item in feedback_entries or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("use_case_name", "")).strip()
        if not name:
            continue
        key = " ".join(name.lower().split())
        rating = item.get("rating")
        comment = str(item.get("comment", "")).strip()
        group = grouped.setdefault(
            key,
            {
                "use_case_name": name,
                "latest_description": str(item.get("description", "")).strip(),
                "rating_count": 0,
                "rating_sum": 0.0,
                "average_rating": None,
                "support_count": 0,
                "concern_count": 0,
                "comments": [],
                "last_updated": timestamp,
            },
        )
        if isinstance(rating, int):
            group["rating_count"] += 1
            group["rating_sum"] += rating
            group["average_rating"] = round(group["rating_sum"] / group["rating_count"], 2)
            if rating >= 4:
                group["support_count"] += 1
            elif rating <= 2:
                group["concern_count"] += 1
        feasibility = item.get("feasibility_feedback") or {}
        if comment or any(feasibility.values()):
            group["comments"].append(
                {
                    "employee": employee_name,
                    "role": role,
                    "department": department,
                    "rating": rating,
                    "comment": comment,
                    "feasibility_feedback": feasibility,
                    "created_at": timestamp,
                }
            )
    return list(grouped.values())


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
    use_cases = report_payload.get("use_cases") or []
    if not use_cases:
        return None
    cl.user_session.set("awaiting_use_case_feedback_consent", True)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("awaiting_use_case_feasibility", False)
    cl.user_session.set("use_case_feedback_index", 0)
    cl.user_session.set("use_case_feedback_entries", [])
    cl.user_session.set("current_use_case_feedback", None)
    cl.user_session.set("current_use_case_feasibility_scope", None)
    invitation = build_use_case_feedback_invitation(use_cases)
    messages.append({"role": "assistant", "content": invitation})
    cl.user_session.set("messages", messages)
    await send_assistant_message(invitation)
    return invitation


async def send_next_use_case_feedback_prompt(send_assistant_message, messages: list):
    report_payload = cl.user_session.get("pending_report_payload") or {}
    use_cases = report_payload.get("use_cases") or []
    index = int(cl.user_session.get("use_case_feedback_index", 0) or 0)
    if index >= len(use_cases):
        return None
    prompt = build_use_case_rating_prompt(use_cases[index], index + 1, len(use_cases))
    cl.user_session.set("awaiting_use_case_opinion", True)
    cl.user_session.set("awaiting_use_case_rating", False)
    cl.user_session.set("awaiting_use_case_feasibility", False)
    messages.append({"role": "assistant", "content": prompt})
    cl.user_session.set("messages", messages)
    await send_assistant_message(prompt)
    return prompt


async def close_interview(send_assistant_message, messages: list, transcript: str, analysis_transcript: str, seniority_level: str, interview_count: int, report_payload: dict = None, use_case_feedback: list = None):
    cl.user_session.set("report_done", True)
    cl.user_session.set("collection_step", "__closed__")
    cl.user_session.set("awaiting_final_confirmation", False)

    closing_msg = "Thank you for your time. Your answers will be taken into consideration. This interview is now complete."
    messages.append({"role": "assistant", "content": closing_msg})
    cl.user_session.set("messages", messages)
    await send_assistant_message(closing_msg)
    try:
        draft_id = cl.user_session.get("active_draft_id")
        if draft_id:
            delete_interview_checkpoint(str(draft_id))
    except Exception:
        traceback.print_exc()

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
        metadata = cl.user_session.get("metadata")
        md_content = generate_markdown_report(report, metadata)
        md_content = append_use_case_feedback_markdown(md_content, use_case_feedback or [])
        report_json = serialize_report_payload(report, use_case_feedback or [])
        file_locations = persist_report_files(session_id, report_json, md_content)
        save_session(
            company=metadata["company"],
            employee=metadata["employee_name"],
            department=metadata["department"],
            role=metadata["role"],
            seniority_level=seniority_level,
            transcript=transcript,
            report_json=report_json,
            report_md=md_content,
        )
        north_star = report.north_star_alignment if report.north_star_alignment and interview_count == 0 else None
        update_company_insights(
            company=metadata["company"],
            north_star=north_star,
            tasks=[t.model_dump() for t in report.tasks],
            use_cases=[uc.model_dump() for uc in report.use_cases],
            validated_use_cases=build_validated_use_case_entries(use_case_feedback or [], metadata),
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
    except Exception:
        traceback.print_exc()
        return
    finally:
        cl.user_session.set("pending_report_payload", None)
        cl.user_session.set("awaiting_use_case_feedback_consent", False)
        cl.user_session.set("awaiting_use_case_opinion", False)
        cl.user_session.set("awaiting_use_case_rating", False)
        cl.user_session.set("awaiting_use_case_feasibility", False)
        cl.user_session.set("use_case_feedback_index", 0)
        cl.user_session.set("use_case_feedback_entries", [])
        cl.user_session.set("current_use_case_feedback", None)
        cl.user_session.set("current_use_case_feasibility_scope", None)
