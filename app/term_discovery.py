import json
import os
import re
from typing import Optional

import requests

from ai_client import MODEL, get_client

REQUEST_TIMEOUT_SECONDS = 10

COMMON_TERMS_NO_CLARIFY = {
    "api",
    "apis",
    "bi",
    "b2b",
    "b2c",
    "crm",
    "crms",
    "csv",
    "dashboard",
    "dashboards",
    "erp",
    "erps",
    "etl",
    "hris",
    "kpi",
    "kpis",
    "lms",
    "okr",
    "okrs",
    "pdf",
    "pdfs",
    "rfp",
    "rfps",
    "roi",
    "saas",
    "sla",
    "slas",
    "sql",
}

SILENT_PUBLIC_TOOL_TERMS = {
    "airtable",
    "asana",
    "crunchbase",
    "excel",
    "figma",
    "github",
    "gitlab",
    "google sheets",
    "hubspot",
    "ivalua",
    "jira",
    "linkedin",
    "looker",
    "microsoft teams",
    "netsuite",
    "notion",
    "overleaf",
    "power bi",
    "salesforce",
    "sap",
    "servicenow",
    "sharepoint",
    "slack",
    "smartsheet",
    "snowflake",
    "tableau",
    "teams",
    "workday",
}

def _normalize_term_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return text.strip(" \t\r\n.,:;!?()[]{}\"'")


def _is_common_no_clarify_term(term: str) -> bool:
    return _normalize_term_name(term) in COMMON_TERMS_NO_CLARIFY


def _should_silently_capture_public_term(term: str) -> bool:
    return _normalize_term_name(term) in SILENT_PUBLIC_TOOL_TERMS


def infer_public_term_context(term: str, company: str = "") -> Optional[str]:
    prompt = {
        "term": term,
        "company": company,
    }
    system_prompt = """You infer likely public context for a named tool or platform mentioned in a work interview.

Rules:
- Only return a likely public description when the term is a well-known public product, platform, or service.
- If the term is ambiguous, internal-sounding, or not confidently identifiable, return JSON with found=false.
- Keep the description to 1-2 cautious sentences.
- If relevant, mention uncertainty briefly instead of pretending certainty.
- Return only valid JSON with:
  - found: boolean
  - context: string
"""
    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        if bool(data.get("found")):
            context = str(data.get("context", "") or "").strip()
            return context or None
    except Exception:
        pass
    return None


def _known_term_names(metadata: dict) -> set[str]:
    items = metadata.get("term_contexts") or []
    names = set()
    for item in items:
        if isinstance(item, dict):
            name = _normalize_term_name(item.get("term", ""))
            if name:
                names.add(name)
    return names


def identify_term_candidate(user_message: str, history: Optional[list], metadata: Optional[dict]) -> dict:
    recent_history = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in (history or [])[-8:]
        if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
    ]

    payload = {
        "user_message": user_message,
        "recent_history": recent_history,
        "metadata": {
            "company": (metadata or {}).get("company", ""),
            "role": (metadata or {}).get("role", ""),
            "department": (metadata or {}).get("department", ""),
            "known_terms": sorted(_known_term_names(metadata or {})),
        },
    }

    system_prompt = """You detect whether the interviewee just introduced an important named tool, system, acronym, or workflow term.

Return JSON with:
- found_term: boolean
- should_clarify: boolean
- term: string

Rules:
- Set found_term=true only when the message introduces a term that matters for understanding the work.
- Set should_clarify=true only when the term is likely internal, company-specific, niche, or ambiguous enough that the interviewer cannot safely continue without a short explanation.
- Prefer should_clarify=false for common business or technical terms such as CRM, KPI, OKR, SLA, ERP, API, SQL, RFP, PDF, dashboards, and spreadsheets.
- Prefer should_clarify=false for well-known public tools or platforms such as Salesforce, HubSpot, Jira, LinkedIn, Excel, Google Sheets, SAP, Workday, ServiceNow, Overleaf, and Ivalua.
- Do not flag ordinary words, company names already handled separately, or terms already clarified in known_terms.
- If the user already explained what the term does in the same message, should_clarify=false.
- If there is no meaningful candidate term, set found_term=false and term="".
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
        found_term = bool(data.get("found_term", False))
        should = bool(data.get("should_clarify", False))
        term = str(data.get("term", "")).strip()
        if term:
            if _is_common_no_clarify_term(term):
                return {
                    "found_term": True,
                    "should_clarify": False,
                    "term": term,
                    "capture_public_context": False,
                }
            if _should_silently_capture_public_term(term):
                return {
                    "found_term": True,
                    "should_clarify": False,
                    "term": term,
                    "capture_public_context": True,
                }
        if found_term and term:
            return {
                "found_term": True,
                "should_clarify": should,
                "term": term,
                "capture_public_context": False,
            }
    except Exception:
        pass

    return {
        "found_term": False,
        "should_clarify": False,
        "term": "",
        "capture_public_context": False,
    }


def search_term_with_serpapi(term: str, company: str = "") -> Optional[str]:
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return None

    query = f"{term} {company} tool software" if company else f"{term} tool software"
    try:
        response = requests.get(
            "https://serpapi.com/search",
            params={"q": query, "api_key": api_key, "num": 3},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if "knowledge_graph" in data and data["knowledge_graph"].get("description"):
            return data["knowledge_graph"]["description"]
        if "answer_box" in data and data["answer_box"].get("answer"):
            return data["answer_box"]["answer"]
        snippets = [r.get("snippet", "") for r in data.get("organic_results", [])[:3] if r.get("snippet")]
        return " ".join(snippets).strip() or None
    except Exception:
        return None


def search_term_with_ddg(term: str, company: str = "") -> Optional[str]:
    query = f"{term} {company}" if company else term
    try:
        response = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if data.get("Abstract"):
            return data["Abstract"]
        if data.get("Definition"):
            return data["Definition"]
        for item in data.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                return item["Text"]
    except Exception:
        return None
    return None


def synthesize_term_context(term: str, company: str, public_snippet: str) -> Optional[str]:
    if not public_snippet:
        return None
    prompt = {
        "term": term,
        "company": company,
        "public_snippet": public_snippet,
    }
    system_prompt = """You summarize public context for a named tool or term mentioned in an interview.

Rules:
- Keep it to 1-2 sentences.
- Be cautious. If the snippet is weak or ambiguous, say that this is only possible public context.
- Do not claim certainty about internal company tools.
"""
    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            temperature=0,
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception:
        return public_snippet


def lookup_term_context(term: str, company: str = "") -> Optional[str]:
    serp = search_term_with_serpapi(term, "")
    if serp:
        return synthesize_term_context(term, "", serp)
    ddg = search_term_with_ddg(term, "")
    if ddg:
        return synthesize_term_context(term, "", ddg)

    if company:
        serp = search_term_with_serpapi(term, company)
        if serp:
            return synthesize_term_context(term, company, serp)
        ddg = search_term_with_ddg(term, company)
        if ddg:
            return synthesize_term_context(term, company, ddg)

    return infer_public_term_context(term, company)


def build_term_clarification_prompt(term: str, public_context: Optional[str]) -> str:
    if public_context:
        return (
            f'You mentioned "{term}".\n\n'
            f"From the context, this may refer to: {public_context}\n\n"
            f'Is that the same "{term}" you mean in your workflow? If not, what is it and what do you mainly use it for?'
        )
    return (
        f'You mentioned "{term}".\n\n'
        f'What is "{term}", and what do you mainly use it for in your work?'
    )


def save_term_context(metadata: dict, term: str, public_context: str, user_explanation: str) -> dict:
    items = list(metadata.get("term_contexts") or [])
    normalized_term = _normalize_term_name(term)
    for item in items:
        if not isinstance(item, dict):
            continue
        if _normalize_term_name(item.get("term", "")) != normalized_term:
            continue
        existing_public_context = str(item.get("public_context", "") or "").strip()
        existing_user_explanation = str(item.get("user_explanation", "") or "").strip()
        if public_context and not existing_public_context:
            item["public_context"] = public_context
        if user_explanation and (
            not existing_user_explanation or existing_user_explanation == existing_public_context
        ):
            item["user_explanation"] = user_explanation
        metadata["term_contexts"] = items
        return metadata
    items.append(
        {
            "term": term,
            "public_context": public_context or "",
            "user_explanation": user_explanation or "",
        }
    )
    metadata["term_contexts"] = items
    return metadata
