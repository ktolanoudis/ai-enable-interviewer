import json
import re
from typing import Optional

from ai_client import MODEL, get_client


def _fallback_parse(field: str, user_message: str) -> dict:
    text = str(user_message or "").strip()

    if field == "email":
        if _looks_like_privacy_refusal(text):
            return {"intent": "skip", "value": ""}
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
            return {"intent": "provide", "value": text.lower()}
        if "@" in text:
            return {"intent": "invalid", "value": text}

    if field in {"department", "role"}:
        return {"intent": "provide", "value": normalize_collection_value(field, text)}

    return {"intent": "provide", "value": text}


def _looks_like_privacy_refusal(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not normalized:
        return False
    refusal_markers = [
        "prefer not",
        "rather not",
        "not share",
        "don't want to share",
        "do not want to share",
        "cannot share",
        "can't share",
        "won't share",
        "will not share",
        "privacy",
        "personal email",
    ]
    return any(marker in normalized for marker in refusal_markers)


def _title_case_metadata_value(value: str) -> str:
    words = str(value or "").strip().split()
    if not words:
        return ""
    lowercase_words = {"and", "of", "or", "the", "to", "for", "in"}
    titled = []
    for index, word in enumerate(words):
        if word.isupper():
            titled.append(word)
        elif index > 0 and word.lower() in lowercase_words:
            titled.append(word.lower())
        else:
            titled.append(word[:1].upper() + word[1:])
    return " ".join(titled)


def normalize_collection_value(field: str, value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""

    normalized = text.lower().strip(" .")
    patterns = [
        r"^(?:my\s+)?(?:department|team|function)\s+is\s+(.+)$",
        r"^(?:i\s+work|i'm|i\s+am|im)\s+in\s+(?:the\s+)?(.+?)(?:\s+department|\s+team)?$",
        r"^(?:my\s+)?(?:position|role|job|job\s+title)\s+is\s+(.+)$",
        r"^(?:i\s+work\s+as|i'm|i\s+am|im)\s+(?:a|an|the)\s+(.+)$",
        r"^(?:i\s+work\s+as|i'm|i\s+am|im)\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, normalized, re.I)
        if match:
            normalized = match.group(1).strip(" .")
            break

    department_from_role = {
        "teacher": "Teaching",
        "language teacher": "Teaching",
        "greek teacher": "Teaching",
        "tutor": "Teaching",
        "instructor": "Teaching",
        "professor": "Teaching",
        "educator": "Teaching",
        "software engineer": "Engineering",
        "developer": "Engineering",
        "engineer": "Engineering",
        "salesperson": "Sales",
        "sales associate": "Sales",
        "customer service associate": "Customer Service",
        "support agent": "Customer Support",
    }

    if field == "department" and normalized in department_from_role:
        return department_from_role[normalized]

    return _title_case_metadata_value(normalized)


def _deterministic_parse(field: str, user_message: str) -> Optional[dict]:
    text = str(user_message or "").strip()
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if not normalized:
        return {"intent": "invalid" if field == "email" else "skip", "value": ""}

    anonymous_phrases = {
        "anonymous",
        "prefer not to say",
        "i prefer not to say",
        "i want to remain anonymous",
        "rather not say",
    }
    skip_phrases = {"skip", "not now", "none", "n/a", "na", "no", "no thanks", "don't know", "i don't know"}
    if normalized in anonymous_phrases:
        return {"intent": "anonymous", "value": ""}
    if normalized in skip_phrases:
        return {"intent": "skip", "value": ""}

    if field == "email":
        if _looks_like_privacy_refusal(text):
            return {"intent": "skip", "value": ""}
        email_match = re.search(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", text)
        if email_match:
            return {"intent": "provide", "value": email_match.group(0).lower()}
        if "@" in text:
            return {"intent": "invalid", "value": text}
        return None

    if field == "company_website":
        url_match = re.search(r"(https?://[^\s,;<>]+|(?:www\.)?[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?:/[^\s,;<>]*)?)", text, re.I)
        if url_match:
            return {"intent": "provide", "value": url_match.group(0)}
        if normalized in {"no website", "no site", "unknown", "not sure"}:
            return {"intent": "skip", "value": ""}
        return None

    if field == "name" and normalized.startswith("my name is "):
        return {"intent": "provide", "value": text[11:].strip()}
    if field == "company" and normalized.startswith("i work for "):
        return {"intent": "provide", "value": text[11:].strip()}
    if field in {"department", "role"} and len(text.split()) <= 8:
        return {"intent": "provide", "value": normalize_collection_value(field, text)}
    return None


def parse_collection_response(
    field: str,
    user_message: str,
    history: Optional[list] = None,
) -> dict:
    deterministic = _deterministic_parse(field, user_message)
    if deterministic is not None:
        return deterministic

    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-6:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    system_prompt = """You classify user replies during metadata collection for an interview app.

Return JSON with:
- intent: one of "provide", "skip", "anonymous", "invalid"
- value: normalized field value or empty string

Rules:
- Respect the user's meaning, not just exact keywords.
- "anonymous", "prefer not to say", "i want to remain anonymous" should map to intent="anonymous".
- "skip", "not now", "none", "n/a" should map to intent="skip" when that makes sense for the field.
- For email:
  - if the text is a valid email, use intent="provide" and lowercase it
  - if it is clearly meant as a refusal/privacy request, use "anonymous" or "skip"
  - if it looks like a malformed email attempt, use "invalid"
- For company website:
  - plain refusals or missing-site answers should become "skip"
- For name:
  - anonymous/privacy requests should become "anonymous"
- For company, department, and role:
  - privacy/refusal requests can become "anonymous" or "skip"
  - strip conversational wrappers like "I am a teacher", "my role is analyst", or "I work in operations"
  - department should be a functional area, not a full sentence
  - role should be the job title, not a full sentence
- If the user actually provides the requested value in natural language, extract the value cleanly.
- Return only valid JSON.
"""

    payload = {
        "field": field,
        "user_message": user_message,
        "recent_history": recent_history,
    }

    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intent = str(data.get("intent", "")).strip().lower()
        value = str(data.get("value", "")).strip()
        if intent not in {"provide", "skip", "anonymous", "invalid"}:
            return _fallback_parse(field, user_message)
        if intent == "provide" and field in {"department", "role"}:
            value = normalize_collection_value(field, value)
        return {"intent": intent, "value": value}
    except Exception:
        return _fallback_parse(field, user_message)
