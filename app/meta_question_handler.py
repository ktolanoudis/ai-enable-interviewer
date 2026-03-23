"""Model-based message classification and response helpers for the interview flow."""

import json
from typing import Optional

from ai_client import MODEL, get_client

def generate_meta_response(
    user_message: str,
    current_question_context: str = "",
    history: Optional[list] = None,
) -> Optional[str]:
    """
    Generate appropriate response to meta-questions.
    
    Args:
        user_message: User's meta-question
        current_question_context: What we were asking about (e.g., "your role", "North Star")
    
    Returns:
        Response string, or None if can't handle
    """
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You are helping with a live interview.

The user just asked a clarification or process question. Answer briefly and contextually based on the most recent assistant question and conversation history.

Rules:
- Do not restart the interview.
- Do not give a generic onboarding explanation unless the history truly lacks context.
- Answer the user's clarification directly.
- If the user asks what you mean, explain the immediately previous assistant question in plain language.
- End by gently restating the current question or giving one concrete way they can answer it.
- Keep it concise: 2-5 sentences.
"""

    payload = {
        "current_question_context": current_question_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        return "I mean the question I just asked in a practical sense. A short, simple answer is enough."


def classify_message_intent(
    user_message: str,
    current_question_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You classify the user's latest message in a live interview.

Return JSON with:
- intent: one of "answer", "clarification", "uncertain", "correction"

Rules:
- "clarification" means the user is asking what the previous question means or how to answer it.
- "uncertain" means the user cannot answer, does not know, wants to move on, or is stuck.
- "correction" means the user is directly saying previously stated information is wrong.
- "answer" means a normal substantive answer, even if short.
- Use the recent conversation context, not just keywords.
- Return only valid JSON.
"""

    payload = {
        "current_question_context": current_question_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intent = str(data.get("intent", "")).strip().lower()
        if intent in {"answer", "clarification", "uncertain", "correction"}:
            return {"intent": intent}
    except Exception:
        pass

    return {"intent": "answer"}


def classify_use_case_feedback_response(
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You classify the user's latest message during an AI use-case feedback step.

Return JSON with:
- intent: one of "opinion", "clarification", "uncertain", "structural_feedback", "scope_mismatch"

Rules:
- "opinion" means the user gave a practical reaction to the use case and the interview can move on to the rating question.
- "clarification" means the user is asking how the proposed use case would work, what it means, what data/model it would use, or otherwise needs explanation before giving feedback.
- "uncertain" means the user cannot judge it, does not know, or cannot answer yet.
- "structural_feedback" means the user is commenting on how the proposed use cases should be combined, split, reordered, or otherwise reframed before rating this one.
- "scope_mismatch" means the user is saying this use case mainly belongs to another role, team, or owner, so it is not really part of their work.
- If the message contains both a positive/negative reaction and a follow-up question that still needs answering, classify it as "clarification".
- If the message mainly says this use case overlaps with another, should be merged, is redundant, or needs reframing, classify it as "structural_feedback".
- If the message mainly says this task belongs to a manager or another person/team, classify it as "scope_mismatch".
- Use the recent conversation and use case context, not keywords alone.
- Return only valid JSON.
"""

    payload = {
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intent = str(data.get("intent", "")).strip().lower()
        if intent in {"opinion", "clarification", "uncertain", "structural_feedback", "scope_mismatch"}:
            return {"intent": intent}
    except Exception:
        pass

    return {"intent": "opinion"}


def interpret_use_case_opinion_response(
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You interpret the user's latest message during an AI use-case feedback step.

Return JSON with:
- has_substantive_opinion: boolean
- opinion_text: string
- included_rating: integer 1-5, "skip", or null

Rules:
- The user may give both their practical opinion and rating in the same message.
- "has_substantive_opinion" should be true only if the message contains a meaningful practical reaction in words, not just a number or a very short score-only phrase.
- If the message includes both a rating and an opinion, keep only the practical opinion in "opinion_text" and extract the rating separately.
- If the message is only a rating or effectively only a rating, set "has_substantive_opinion" to false and leave "opinion_text" empty.
- Preserve the user's meaning in "opinion_text"; do not add new facts.
- Return only valid JSON.
"""

    payload = {
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        has_substantive_opinion = bool(data.get("has_substantive_opinion"))
        opinion_text = str(data.get("opinion_text", "") or "").strip()
        included_rating = data.get("included_rating")
        if included_rating == "skip":
            normalized_rating = "skip"
        elif isinstance(included_rating, int) and 1 <= included_rating <= 5:
            normalized_rating = included_rating
        else:
            normalized_rating = None
        return {
            "has_substantive_opinion": has_substantive_opinion,
            "opinion_text": opinion_text,
            "included_rating": normalized_rating,
        }
    except Exception:
        return {
            "has_substantive_opinion": False,
            "opinion_text": "",
            "included_rating": None,
        }


def interpret_use_case_rating_response(
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You interpret the user's latest message during an AI use-case rating step.

Return JSON with:
- rating: integer 1-5, "skip", or null
- comment_text: string

Rules:
- The user may provide both a score and extra explanation in the same message.
- Extract the rating when present.
- Put any meaningful explanation, justification, or caveat into "comment_text".
- If the message is only a rating, leave "comment_text" empty.
- Do not invent new facts. Keep the user's meaning.
- Return only valid JSON.
"""

    payload = {
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        rating = data.get("rating")
        if rating == "skip":
            normalized_rating = "skip"
        elif isinstance(rating, int) and 1 <= rating <= 5:
            normalized_rating = rating
        else:
            normalized_rating = None
        comment_text = str(data.get("comment_text", "") or "").strip()
        return {"rating": normalized_rating, "comment_text": comment_text}
    except Exception:
        return {"rating": None, "comment_text": ""}


def generate_use_case_feedback_clarification(
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> Optional[str]:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You are helping in an interview where the user is reviewing a proposed AI use case.

The user asked a clarification question about how the use case would work.

Rules:
- Answer the user's clarification directly and concretely.
- Ground the answer in the proposed use case and the user's job context from the recent history.
- Do not claim implementation details as certain if they were not specified; present them as a plausible approach.
- Keep it concise: 2-5 sentences.
- End by inviting the user to give their practical reaction to the use case.
"""

    payload = {
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        return "A practical version of this would usually use your existing data and workflow tools rather than requiring a fully custom system from scratch. Based on that, how useful does this seem for your work in practice?"


def generate_use_case_feedback_structural_followup(
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> Optional[str]:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You are helping in an interview where the user is reviewing proposed AI use cases.

The user gave structural feedback, such as saying this use case overlaps with another one or should be merged/reframed.

Rules:
- Acknowledge the structural feedback directly.
- Do not dismiss it or immediately ask for a rating.
- Ask one short follow-up question that helps capture the user's reasoning in a practical way.
- Keep it concise: 2-4 sentences.
- The final sentence should be exactly one concrete follow-up question.
"""

    payload = {
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        return "That makes sense. It sounds like you see overlap between these ideas rather than two separate workflows. What part of them feels duplicated to you?"


def generate_use_case_feedback_scope_followup(
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> Optional[str]:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You are helping in an interview where the user is reviewing proposed AI use cases.

The user is saying this use case mainly belongs to someone else's role or team.

Rules:
- Acknowledge that scope/ownership matters.
- Keep it concise: 2-4 sentences.
- Do not argue with the user.
- End with one concrete question that helps capture whether the use case should be treated as low-value for this user's own work or skipped because it is outside their role.
"""

    payload = {
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        return "That makes sense. If this mainly belongs to someone else's role, it may not be very relevant for your own work. Would you treat this as low-value for your work, or would you prefer to skip scoring it?"


def classify_use_case_scope_resolution(
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You classify the user's reply after being asked whether an AI use case is low-value for their own work or outside their role.

Return JSON with:
- intent: one of "outside_role", "low_value", "other"

Rules:
- "outside_role" means the user is saying the use case should be skipped because it belongs to another person's or team's responsibilities.
- "low_value" means the use case is still within scope enough to judge, but they see low usefulness for their day-to-day work.
- "other" means the user did not resolve that distinction clearly.
- Use the recent conversation context, not keywords alone.
- Return only valid JSON.
"""

    payload = {
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intent = str(data.get("intent", "")).strip().lower()
        if intent in {"outside_role", "low_value", "other"}:
            return {"intent": intent}
    except Exception:
        pass

    return {"intent": "other"}


def classify_confirmation_response(
    user_message: str,
    prompt_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You classify the user's reply to a confirmation-style interview prompt.

Return JSON with:
- intent: one of "yes", "no", "correction", "other"

Rules:
- "yes" means clear agreement, confirmation, or willingness to continue.
- "no" means clear refusal, decline, skip, or desire to finish without continuing that step.
- "correction" means the user is saying previously stated information is wrong and needs to be replaced.
- "other" means the user gave extra content instead of a clear yes/no.
- Use the prompt context and recent history, not exact keywords alone.
- Return only valid JSON.
"""

    payload = {
        "prompt_context": prompt_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intent = str(data.get("intent", "")).strip().lower()
        if intent in {"yes", "no", "correction", "other"}:
            return {"intent": intent}
    except Exception:
        pass

    return {"intent": "other"}


def assess_use_case_feasibility_scope(
    use_case_context: str = "",
    metadata: Optional[dict] = None,
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-10:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You assess whether the interviewee is in a good position to judge feasibility aspects of a proposed AI use case.

Return JSON with:
- can_judge_data_quality: boolean
- can_judge_regulatory_risk: boolean
- can_judge_explainability: boolean

Rules:
- Be conservative. Only mark a dimension true if the user likely has enough direct visibility from their role and prior answers.
- Data quality means whether the needed data likely exists, is reliable, or is accessible in practice.
- Regulatory risk means compliance, privacy, legal, or policy constraints.
- Explainability means whether the AI output would need to be understandable, auditable, or easy to justify to others.
- Use the user role, department, and recent conversation context.
- Return only valid JSON.
"""

    payload = {
        "use_case_context": use_case_context,
        "metadata": metadata or {},
        "recent_history": recent_history,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {
            "can_judge_data_quality": bool(data.get("can_judge_data_quality", False)),
            "can_judge_regulatory_risk": bool(data.get("can_judge_regulatory_risk", False)),
            "can_judge_explainability": bool(data.get("can_judge_explainability", False)),
        }
    except Exception:
        return {
            "can_judge_data_quality": False,
            "can_judge_regulatory_risk": False,
            "can_judge_explainability": False,
        }


def extract_use_case_feasibility_feedback(
    user_message: str,
    use_case_context: str = "",
    scope: Optional[dict] = None,
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-10:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You extract structured feasibility feedback about a proposed AI use case from an interviewee's free-text answer.

Return JSON with:
- summary_comment: string
- data_quality_score: integer 1-5 or null
- data_quality_comment: string
- regulatory_risk_level: one of "low", "medium", "high", "critical", "unknown"
- regulatory_comment: string
- explainability_score: integer 1-5 or null
- explainability_comment: string
- safe_to_pursue: one of "yes", "no", "unclear"

Interpretation rules:
- data_quality_score: 1 means data looks unusable/unavailable, 5 means data looks strong and usable.
- explainability_score: 1 means low explainability need, 5 means strong explainability/auditability need.
- If the user clearly says they cannot judge a dimension, use null or "unknown" and note that in the comment.
- Only fill dimensions that are in scope. For out-of-scope dimensions, return null or "unknown".
- Return only valid JSON.
"""

    payload = {
        "use_case_context": use_case_context,
        "scope": scope or {},
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")

        def _score(value):
            if isinstance(value, int) and 1 <= value <= 5:
                return value
            return None

        risk = str(data.get("regulatory_risk_level", "unknown")).strip().lower()
        if risk not in {"low", "medium", "high", "critical", "unknown"}:
            risk = "unknown"
        safe = str(data.get("safe_to_pursue", "unclear")).strip().lower()
        if safe not in {"yes", "no", "unclear"}:
            safe = "unclear"

        return {
            "summary_comment": str(data.get("summary_comment", "")).strip(),
            "data_quality_score": _score(data.get("data_quality_score")),
            "data_quality_comment": str(data.get("data_quality_comment", "")).strip(),
            "regulatory_risk_level": risk,
            "regulatory_comment": str(data.get("regulatory_comment", "")).strip(),
            "explainability_score": _score(data.get("explainability_score")),
            "explainability_comment": str(data.get("explainability_comment", "")).strip(),
            "safe_to_pursue": safe,
        }
    except Exception:
        return {
            "summary_comment": str(user_message or "").strip(),
            "data_quality_score": None,
            "data_quality_comment": "",
            "regulatory_risk_level": "unknown",
            "regulatory_comment": "",
            "explainability_score": None,
            "explainability_comment": "",
            "safe_to_pursue": "unclear",
        }


def extract_single_feasibility_dimension_feedback(
    dimension: str,
    user_message: str,
    use_case_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-10:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You extract one specific feasibility judgment about a proposed AI use case from an interviewee's free-text answer.

Return JSON only.

If dimension = "data_quality", return:
- score: integer 1-5 or null
- comment: string

Interpretation: 1 means very poor or unavailable data, 5 means strong and usable data.

If dimension = "regulatory_risk", return:
- level: one of "low", "medium", "high", "critical", "unknown"
- comment: string

If dimension = "explainability", return:
- score: integer 1-5 or null
- comment: string

Interpretation: 1 means little explainability need, 5 means strong need for explainability, auditability, or justification.

If the user says they cannot judge that dimension, use null or "unknown" and capture that in the comment.
"""

    payload = {
        "dimension": dimension,
        "use_case_context": use_case_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        if dimension == "regulatory_risk":
            level = str(data.get("level", "unknown")).strip().lower()
            if level not in {"low", "medium", "high", "critical", "unknown"}:
                level = "unknown"
            return {"level": level, "comment": str(data.get("comment", "")).strip()}
        score = data.get("score")
        if not (isinstance(score, int) and 1 <= score <= 5):
            score = None
        return {"score": score, "comment": str(data.get("comment", "")).strip()}
    except Exception:
        if dimension == "regulatory_risk":
            return {"level": "unknown", "comment": str(user_message or "").strip()}
        return {"score": None, "comment": str(user_message or "").strip()}


def classify_answer_completeness(
    user_message: str,
    current_question_context: str = "",
    history: Optional[list] = None,
) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You classify whether the user's latest interview answer is too brief to be useful.

Return JSON with:
- intent: one of "sufficient", "too_short"

Rules:
- Consider the latest question and recent conversation context.
- A short answer can still be "sufficient" if it clearly answers the question.
- Use "too_short" only when the answer is too vague or underspecified to move the interview forward.
- Return only valid JSON.
"""

    payload = {
        "current_question_context": current_question_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intent = str(data.get("intent", "")).strip().lower()
        if intent in {"sufficient", "too_short"}:
            return {"intent": intent}
    except Exception:
        pass
    return {"intent": "sufficient"}


def generate_uncertainty_recovery(
    user_message: str,
    current_question_context: str = "",
    history: Optional[list] = None,
) -> Optional[str]:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You are helping with a live interview.

The user could not answer the latest question. Respond naturally and briefly.

Rules:
- Start by acknowledging that it is okay not to know.
- Do not repeat the exact same question.
- Ask one easier adjacent question based on the most recent assistant question.
- Keep the interview moving forward.
- Keep it concise: 2-4 sentences.
- The final sentence should contain exactly one concrete follow-up question.
"""

    payload = {
        "current_question_context": current_question_context,
        "recent_history": recent_history,
        "user_message": user_message,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        return "That's okay, we can move on to the next question.\n\nCould you tell me about the closest related part of your work instead?"
