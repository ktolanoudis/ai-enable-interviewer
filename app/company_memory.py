import json
import re
from typing import List

from ai_client import MODEL, get_client


RECURRING_THEME_CONFIRMATION_THRESHOLD = 2


def _clean_text(value) -> str:
    return str(value or "").strip()


def _slugify_theme_key(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", _clean_text(value).lower())
    return text.strip("_")[:80]


def _normalize_theme_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    label = _clean_text(item.get("label"))
    evidence = _clean_text(item.get("evidence"))
    theme_key = _slugify_theme_key(item.get("theme_key") or label or evidence)
    if not theme_key or not label:
        return None
    category = _clean_text(item.get("category")) or "workflow"
    return {
        "theme_key": theme_key,
        "label": label,
        "category": category,
        "evidence": evidence,
    }


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
  - label: short neutral user-facing theme name
  - category: short descriptive category string if helpful
  - evidence: one sentence summarizing what this employee said

Rules:
- Focus on concrete workflow patterns that could be validated in future interviews.
- Do not force issues into a fixed taxonomy or a predetermined list of theme names.
- Capture the underlying problem, even if the wording is specific to this employee or department.
- Keep labels concise, neutral, and reusable across future interviews at the same company.
- Good examples: approval ping-pong on minor purchases, late process changes after rollout, documentation drift across teams, manual status reconciliation.
- Do not extract generic company descriptions.
- Return at most 5 themes.
- Return only valid JSON.
"""

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
        themes = data.get("themes") or []
        out = []
        for item in themes:
            normalized = _normalize_theme_item(item)
            if normalized:
                out.append(normalized)
        return out[:5]
    except Exception:
        return []


def resolve_recurring_theme(existing_themes: List[dict], new_theme: dict) -> dict | None:
    normalized_new = _normalize_theme_item(new_theme)
    if not normalized_new:
        return None

    candidate_themes = []
    for item in existing_themes or []:
        normalized_existing = _normalize_theme_item(item)
        if not normalized_existing:
            continue
        candidate_themes.append(
            {
                "theme_key": normalized_existing["theme_key"],
                "label": normalized_existing["label"],
                "category": normalized_existing["category"],
                "examples": (item.get("examples") or [])[:2] if isinstance(item.get("examples"), list) else [],
            }
        )

    if not candidate_themes:
        return normalized_new

    payload = {
        "existing_themes": candidate_themes,
        "new_theme": normalized_new,
    }

    system_prompt = """You decide whether a newly extracted workflow theme from one employee interview matches an existing company theme.

Return JSON with:
- action: "merge" or "new"
- target_theme_key: required when action is "merge"
- theme_key: required when action is "new"
- label: required when action is "new"
- category: optional short descriptive string when action is "new"

Rules:
- Merge only when the new theme is clearly the same underlying company issue as an existing theme, even if the wording or department context differs.
- Do not merge merely because both themes are generic process problems.
- If action is "merge", choose the best existing theme_key and do not invent a new one.
- If action is "new", create a neutral short label and stable snake_case key for the underlying issue.
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
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        action = _clean_text(data.get("action")).lower()
        if action == "merge":
            target_theme_key = _slugify_theme_key(data.get("target_theme_key"))
            for item in candidate_themes:
                if item["theme_key"] == target_theme_key:
                    resolved = dict(normalized_new)
                    resolved["theme_key"] = item["theme_key"]
                    resolved["label"] = item["label"]
                    resolved["category"] = item["category"]
                    return resolved
        if action == "new":
            resolved = dict(normalized_new)
            resolved["theme_key"] = _slugify_theme_key(data.get("theme_key") or resolved["theme_key"])
            resolved["label"] = _clean_text(data.get("label")) or resolved["label"]
            resolved["category"] = _clean_text(data.get("category")) or resolved["category"]
            return resolved
    except Exception:
        pass

    normalized_label = " ".join(normalized_new["label"].lower().split())
    for item in candidate_themes:
        if normalized_label == " ".join(item["label"].lower().split()):
            resolved = dict(normalized_new)
            resolved["theme_key"] = item["theme_key"]
            resolved["label"] = item["label"]
            resolved["category"] = item["category"]
            return resolved
    return normalized_new


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
            temperature=0,
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


def assess_theme_relevance(analysis_transcript: str, metadata: dict, notes: dict, existing_themes: List[dict]) -> List[dict]:
    candidate_themes = []
    for item in existing_themes or []:
        if not isinstance(item, dict):
            continue
        theme_key = str(item.get("theme_key", "")).strip().lower()
        label = str(item.get("label", "")).strip()
        category = str(item.get("category", "workflow") or "workflow").strip().lower()
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

    system_prompt = """You assess whether previously observed company themes are plausibly relevant to this interviewee's work.

Return JSON with:
- relevance_assessments: array of objects with:
  - theme_key: string
  - relevance: one of "relevant", "not_relevant", "unclear"
  - evidence: string

Rules:
- Judge relevance based on the interviewee's role, department, tasks, systems, and workflows.
- Use "relevant" only when the theme could plausibly affect this person's actual work.
- Use "not_relevant" when the theme is clearly outside this person's domain or responsibilities.
- Use "unclear" when there is not enough information to tell.
- Be conservative. Do not stretch company-wide issues into roles where they do not realistically apply.
- Return only valid JSON.
"""

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
        out = []
        for item in data.get("relevance_assessments") or []:
            if not isinstance(item, dict):
                continue
            theme_key = str(item.get("theme_key", "")).strip().lower()
            relevance = str(item.get("relevance", "")).strip().lower()
            evidence = str(item.get("evidence", "")).strip()
            if not theme_key or relevance not in {"relevant", "not_relevant", "unclear"}:
                continue
            out.append({"theme_key": theme_key, "relevance": relevance, "evidence": evidence})
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
        net_support = mention_count - contradiction_count
        contradiction_ratio = (contradiction_count / mention_count) if mention_count > 0 else 1.0
        if mention_count >= threshold and net_support > 0 and contradiction_ratio <= 0.5:
            enriched = dict(item)
            enriched["net_support"] = net_support
            enriched["contradiction_ratio"] = contradiction_ratio
            validated.append(enriched)
    return sorted(
        validated,
        key=lambda x: (
            -int(x.get("net_support", 0) or 0),
            -int(x.get("mention_count", x.get("count", 0)) or 0),
            str(x.get("label", "")),
        ),
    )
