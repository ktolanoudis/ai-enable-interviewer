"""
Company Research Module

Automatically researches companies online to provide context for interviews.
"""

import os
import requests
from typing import Optional, Dict
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=False)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
        
        response = requests.get(url, params=params, timeout=10)
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
            max_tokens=200
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
        
        response = requests.get(url, params=params, timeout=10)
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

def research_company(company_name: str, use_ai: bool = True) -> Dict[str, Optional[str]]:
    """
    Research a company using multiple sources
    
    Args:
        company_name: Name of the company
        use_ai: Whether to use OpenAI as fallback (uses API credits)
    
    Returns:
        Dictionary with company info:
        {
            'name': str,
            'description': str or None,
            'source': str (where info came from)
        }
    """
    print(f"\n🔍 Researching company: {company_name}...")
    
    result = {
        'name': company_name,
        'description': None,
        'source': None
    }
    
    # Try SerpAPI first (if configured)
    if os.getenv("SERPAPI_KEY"):
        print("Trying SerpAPI...")
        description = search_company_with_serpapi(company_name)
        if description:
            result['description'] = description
            result['source'] = 'SerpAPI'
            print(f"✓ Found via SerpAPI")
            return result
    
    # Try DuckDuckGo (free, no key required)
    print("Trying DuckDuckGo...")
    description = search_company_simple_web(company_name)
    if description:
        result['description'] = description
        result['source'] = 'DuckDuckGo'
        print(f"✓ Found via DuckDuckGo")
        return result
    
    # Fallback to OpenAI (uses API credits)
    if use_ai:
        print("Trying OpenAI knowledge...")
        description = search_company_with_openai(company_name)
        if description:
            result['description'] = description
            result['source'] = 'OpenAI'
            print(f"✓ Found via OpenAI")
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
    
    return f"""## Company Context: {company_info['name']}

{company_info['description']}

*(Source: {company_info['source']})*

---
"""
