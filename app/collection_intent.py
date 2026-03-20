import json
import re
from typing import Optional

from ai_client import MODEL, get_client


def _fallback_parse(field: str, user_message: str) -> dict:
    text = str(user_message or "").strip()

    if field == "email":
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
            return {"intent": "provide", "value": text.lower()}
        if "@" in text:
            return {"intent": "invalid", "value": text}

    return {"intent": "provide", "value": text}


def parse_collection_response(
    field: str,
    user_message: str,
    history: Optional[list] = None,
) -> dict:
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
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        intent = str(data.get("intent", "")).strip().lower()
        value = str(data.get("value", "")).strip()
        if intent not in {"provide", "skip", "anonymous", "invalid"}:
            return _fallback_parse(field, user_message)
        return {"intent": intent, "value": value}
    except Exception:
        return _fallback_parse(field, user_message)
