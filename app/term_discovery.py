import json
import os
from typing import Optional

import requests

from ai_client import MODEL, get_client

REQUEST_TIMEOUT_SECONDS = 10


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
            name = str(item.get("term", "")).strip().lower()
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

    system_prompt = """You detect whether the interviewee just introduced an important named tool, system, acronym, or workflow term that should be clarified before the interview continues.

Return JSON with:
- should_clarify: boolean
- term: string

Rules:
- Only choose true when the term is likely important for understanding the workflow.
- Good examples: internal tools, CRMs, databases, scheduling tools, proprietary products, domain-specific systems, unexplained acronyms.
- Do not choose true for ordinary words, company names already handled separately, or terms already clarified in known_terms.
- If the user already explained what the term does in the same message, prefer false.
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
        should = bool(data.get("should_clarify", False))
        term = str(data.get("term", "")).strip()
        if should and term:
            return {"should_clarify": True, "term": term}
    except Exception:
        pass

    return {"should_clarify": False, "term": ""}


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
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception:
        return public_snippet


def lookup_term_context(term: str, company: str = "") -> Optional[str]:
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
    items.append(
        {
            "term": term,
            "public_context": public_context or "",
            "user_explanation": user_explanation or "",
        }
    )
    metadata["term_contexts"] = items
    return metadata
