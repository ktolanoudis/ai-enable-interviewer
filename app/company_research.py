"""
Company Research Module

Automatically researches companies online to provide context for interviews.
"""

import os
import re
from html import unescape
from urllib.parse import urlparse
import requests
from typing import Optional, Dict, List
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=False)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
REQUEST_TIMEOUT_SECONDS = 10
MAX_WEBSITE_TEXT_CHARS = 8000


def normalize_website_url(website_url: str) -> str:
    """Normalize user-provided URL into a fetchable https URL."""
    url = (website_url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _extract_text_from_html(html: str) -> str:
    """Strip scripts/styles/tags and return compact plain text."""
    if not html:
        return ""
    cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
    cleaned = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def fetch_company_website_text(website_url: str) -> Optional[str]:
    """
    Fetch and extract plain text from the provided website URL.
    """
    normalized_url = normalize_website_url(website_url)
    if not normalized_url:
        return None

    try:
        resp = requests.get(
            normalized_url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CompanyResearchBot/1.0)"},
        )
        if resp.status_code != 200:
            return None
        text = _extract_text_from_html(resp.text)
        if not text:
            return None
        return text[:MAX_WEBSITE_TEXT_CHARS]
    except Exception as e:
        print(f"Website fetch failed: {e}")
        return None

def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)

def search_company_with_serpapi(company_name: str) -> Optional[str]:
    """
    Search for company using SerpAPI (requires SERPAPI_KEY)
    
    Args:
        company_name: Name of the company
    
    Returns:
        Company description or None
    """
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return None
    
    try:
        url = "https://serpapi.com/search"
        params = {
            "q": f"{company_name} company what do they do",
            "api_key": api_key,
            "num": 3
        }
        
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 200:
            data = response.json()
            
            # Extract answer box or knowledge graph
            if "answer_box" in data:
                return data["answer_box"].get("answer", "")
            
            if "knowledge_graph" in data:
                kg = data["knowledge_graph"]
                description = kg.get("description", "")
                if description:
                    return description
            
            # Extract from organic results
            if "organic_results" in data and len(data["organic_results"]) > 0:
                snippets = [r.get("snippet", "") for r in data["organic_results"][:3]]
                return " ".join(snippets)
        
    except Exception as e:
        print(f"SerpAPI search failed: {e}")
    
    return None

def search_company_with_openai(company_name: str) -> Optional[str]:
    """
    Use OpenAI to generate company description based on its training data
    
    Args:
        company_name: Name of the company
    
    Returns:
        Company description or None
    """
    try:
        client = _client()
        
        prompt = f"""Provide a brief 2-3 sentence description of the company '{company_name}'.

Include:
- What the company does (products/services)
- Industry/sector
- Notable characteristics (if well-known)

If you don't know this company, respond with: "Unknown company"

Do not make up information. Only use what you know from your training data."""
        
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides factual company information."},
                {"role": "user", "content": prompt}
            ],
            temperature=1,
            max_completion_tokens=200
        )
        
        description = response.choices[0].message.content.strip()
        
        if "unknown" in description.lower() or "don't know" in description.lower():
            return None
        
        return description
        
    except Exception as e:
        print(f"OpenAI search failed: {e}")
        return None

def search_company_simple_web(company_name: str) -> Optional[str]:
    """
    Simple web search using DuckDuckGo Instant Answer API (no key required)
    
    Args:
        company_name: Name of the company
    
    Returns:
        Company description or None
    """
    try:
        url = "https://api.duckduckgo.com/"
        params = {
            "q": f"{company_name} company",
            "format": "json",
            "no_redirect": 1
        }
        
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 200:
            data = response.json()
            
            # Try Abstract (company description)
            if data.get("Abstract"):
                return data["Abstract"]
            
            # Try Definition
            if data.get("Definition"):
                return data["Definition"]
            
            # Try first RelatedTopic
            if data.get("RelatedTopics") and len(data["RelatedTopics"]) > 0:
                first_topic = data["RelatedTopics"][0]
                if "Text" in first_topic:
                    return first_topic["Text"]
        
    except Exception as e:
        print(f"DuckDuckGo search failed: {e}")
    
    return None

def _build_company_summary_with_openai(
    company_name: str,
    website_url: Optional[str],
    website_text: Optional[str],
    external_snippets: List[str],
) -> Optional[str]:
    """
    Build a concise company profile from website + external context.
    """
    try:
        client = _client()

        snippets_joined = "\n\n".join([s for s in external_snippets if s][:3]).strip()
        website_context = website_text or ""
        website_url_text = website_url or "Not provided"

        prompt = f"""You are preparing context for an AI workflow interview.

Company: {company_name}
Company website: {website_url_text}

Website text excerpt:
{website_context}

External web snippets:
{snippets_joined}

Write a short summary (2-4 sentences) that covers:
- likely industry
- products/services
- business model or target customers (if inferable)

Use only evidence from the provided context. If unclear, say uncertain rather than guessing."""

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You summarize company context for interviews and avoid speculation."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_completion_tokens=220,
        )
        content = (response.choices[0].message.content or "").strip()
        return content or None
    except Exception as e:
        print(f"OpenAI synthesis failed: {e}")
        return None


def research_company(
    company_name: str,
    company_website: Optional[str] = None,
    use_ai: bool = True,
) -> Dict[str, Optional[str]]:
    """
    Research a company using multiple sources
    
    Args:
        company_name: Name of the company
        company_website: Optional company website URL from user
        use_ai: Whether to use OpenAI as fallback (uses API credits)
    
    Returns:
        Dictionary with company info:
        {
            'name': str,
            'description': str or None,
            'source': str (where info came from)
            'website_url': str or None
        }
    """
    print(f"\n🔍 Researching company: {company_name}...")
    normalized_website = normalize_website_url(company_website or "")
    
    result = {
        'name': company_name,
        'description': None,
        'source': None,
        'website_url': normalized_website or None,
    }

    external_snippets: List[str] = []

    website_text = None
    if normalized_website:
        print(f"Trying company website: {normalized_website}...")
        website_text = fetch_company_website_text(normalized_website)
        if website_text:
            external_snippets.append(f"Website excerpt: {website_text}")

    # Try SerpAPI first (if configured) for external context.
    if os.getenv("SERPAPI_KEY"):
        print("Trying SerpAPI...")
        serp_desc = search_company_with_serpapi(company_name)
        if serp_desc:
            external_snippets.append(f"SerpAPI results: {serp_desc}")

    # Try DuckDuckGo (free, no key required) for external context.
    print("Trying DuckDuckGo...")
    ddg_desc = search_company_simple_web(company_name)
    if ddg_desc:
        external_snippets.append(f"DuckDuckGo result: {ddg_desc}")

    if use_ai and (website_text or external_snippets):
        print("Synthesizing company profile with OpenAI...")
        synthesized = _build_company_summary_with_openai(
            company_name=company_name,
            website_url=normalized_website,
            website_text=website_text,
            external_snippets=external_snippets,
        )
        if synthesized:
            result["description"] = synthesized
            source_parts = []
            if website_text:
                source_parts.append("Company website")
            if ddg_desc:
                source_parts.append("DuckDuckGo")
            if os.getenv("SERPAPI_KEY") and any(s.startswith("SerpAPI results:") for s in external_snippets):
                source_parts.append("SerpAPI")
            source_parts.append("OpenAI synthesis")
            result["source"] = " + ".join(source_parts)
            print("✓ Built company summary from multiple sources")
            return result

    # Fallbacks when synthesis is unavailable.
    if website_text:
        result["description"] = website_text[:800]
        result["source"] = "Company website"
        print("✓ Using company website text")
        return result
    if ddg_desc:
        result["description"] = ddg_desc
        result["source"] = "DuckDuckGo"
        print("✓ Found via DuckDuckGo")
        return result

    if use_ai:
        print("Trying OpenAI knowledge fallback...")
        description = search_company_with_openai(company_name)
        if description:
            result["description"] = description
            result["source"] = "OpenAI"
            print("✓ Found via OpenAI")
            return result
    
    print("✗ No information found")
    return result

def format_company_context(company_info: Dict) -> str:
    """
    Format company research for display to user
    
    Args:
        company_info: Result from research_company()
    
    Returns:
        Formatted string for chat display
    """
    if not company_info.get('description'):
        return ""
    
    website_line = ""
    if company_info.get("website_url"):
        parsed = urlparse(company_info["website_url"])
        website_line = f"Website reviewed: {parsed.netloc or company_info['website_url']}\n\n"

    return f"""## Company Context: {company_info['name']}

{website_line}{company_info['description']}

*(Source: {company_info['source']})*

---
"""
