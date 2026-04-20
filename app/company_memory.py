import json
from typing import List

from ai_client import MODEL, get_client


RECURRING_THEME_CONFIRMATION_THRESHOLD = 2


def extract_company_recurring_themes(analysis_transcript: str, metadata: dict, notes: dict) -> List[dict]:
    payload = {
        "company": metadata.get("company", ""),
        "department": metadata.get("department", ""),
        "role": metadata.get("role", ""),
        "notes": notes or {},
        "analysis_transcript": analysis_transcript,
    }

    system_prompt = """You extract company-level recurring interview themes from one employee interview.

Return JSON with:
- themes: array of objects with:
  - theme_key: short stable snake_case identifier
  - label: short user-facing theme name
  - category: one of "friction", "manual_step", "tool", "approval", "coordination", "data", "goal", "constraint"
  - evidence: one sentence summarizing what this employee said

Rules:
- Focus on concrete workflow patterns that could be validated in future interviews.
- Good examples: approval bottlenecks, manual contract review, fragmented tooling, repeated data entry, slow applicant response, spending approval delays.
- Do not extract generic company descriptions.
- Return at most 5 themes.
- Prefer stable keys such as approval_bottlenecks, manual_contract_review, fragmented_tooling.
- Return only valid JSON.
"""

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
        themes = data.get("themes") or []
        out = []
        for item in themes:
            if not isinstance(item, dict):
                continue
            theme_key = str(item.get("theme_key", "")).strip().lower()
            label = str(item.get("label", "")).strip()
            category = str(item.get("category", "")).strip().lower()
            evidence = str(item.get("evidence", "")).strip()
            if not theme_key or not label:
                continue
            if category not in {"friction", "manual_step", "tool", "approval", "coordination", "data", "goal", "constraint"}:
                category = "friction"
            out.append(
                {
                    "theme_key": theme_key,
                    "label": label,
                    "category": category,
                    "evidence": evidence,
                }
            )
        return out[:5]
    except Exception:
        return []


def assess_theme_alignment(analysis_transcript: str, metadata: dict, notes: dict, existing_themes: List[dict]) -> List[dict]:
    candidate_themes = []
    for item in existing_themes or []:
        if not isinstance(item, dict):
            continue
        theme_key = str(item.get("theme_key", "")).strip().lower()
        label = str(item.get("label", "")).strip()
        category = str(item.get("category", "friction") or "friction").strip().lower()
        if not theme_key or not label:
            continue
        candidate_themes.append(
            {
                "theme_key": theme_key,
                "label": label,
                "category": category,
                "examples": item.get("examples", [])[:2] if isinstance(item.get("examples"), list) else [],
            }
        )
    if not candidate_themes:
        return []

    payload = {
        "company": metadata.get("company", ""),
        "department": metadata.get("department", ""),
        "role": metadata.get("role", ""),
        "notes": notes or {},
        "analysis_transcript": analysis_transcript,
        "candidate_themes": candidate_themes,
    }

    system_prompt = """You assess whether an employee interview confirms or contradicts previously observed company themes.

Return JSON with:
- alignments: array of objects with:
  - theme_key: string
  - stance: one of "confirm", "contradict", "not_mentioned"
  - evidence: string

Rules:
- Use "confirm" only if the interview clearly supports that the theme affects this person's work.
- Use "contradict" only if the interview clearly suggests the opposite or that the issue does not apply to this person's work.
- Use "not_mentioned" if there is not enough evidence either way.
- Be conservative.
- Return only valid JSON.
"""

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
        out = []
        for item in data.get("alignments") or []:
            if not isinstance(item, dict):
                continue
            theme_key = str(item.get("theme_key", "")).strip().lower()
            stance = str(item.get("stance", "")).strip().lower()
            evidence = str(item.get("evidence", "")).strip()
            if not theme_key or stance not in {"confirm", "contradict", "not_mentioned"}:
                continue
            out.append({"theme_key": theme_key, "stance": stance, "evidence": evidence})
        return out
    except Exception:
        return []


def get_validated_recurring_themes(themes: List[dict], threshold: int = RECURRING_THEME_CONFIRMATION_THRESHOLD) -> List[dict]:
    validated = []
    for item in themes or []:
        if not isinstance(item, dict):
            continue
        mention_count = int(item.get("mention_count", item.get("count", 0)) or 0)
        contradiction_count = int(item.get("contradiction_count", 0) or 0)
        if mention_count >= threshold:
            enriched = dict(item)
            enriched["net_support"] = mention_count - contradiction_count
            validated.append(enriched)
    return sorted(
        validated,
        key=lambda x: (
            -int(x.get("net_support", 0) or 0),
            -int(x.get("mention_count", x.get("count", 0)) or 0),
            str(x.get("label", "")),
        ),
    )
