# interview_agent.py
import os, json
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=False)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set (.env or exported).")

    base_url = os.getenv("OPENAI_BASE_URL")  # optional (LiteLLM proxy)
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)

def build_notes_extractor_prompt(seniority_level: str, company_context: Optional[Dict] = None) -> str:
    """
    Build role-aware notes extraction prompt with company context
    
    Args:
        seniority_level: executive/senior/intermediate/junior/intern
        company_context: Previous interviews data (north_star, tasks, use_cases)
    
    Returns:
        Customized system prompt
    """
    
    base_prompt = """
You are extracting information for an AI Use Case Discovery framework following academic research methodology.

This framework has 4 steps:
STEP 2: Identify and break down day-to-day work into granular tasks
STEP 3: Match tasks with AI use case opportunities aligned with business goals
STEP 4: Define KPIs for measuring success
STEP 5: Evaluate feasibility (data quality, regulatory risk, technical complexity)

Extract structured notes AND what is missing. Return STRICT JSON only.

Schema:
{
  "role": string|null,
  "department": string|null,
  "north_star_context": string|null,  // Business goals, strategic priorities mentioned
  
  // STEP 2: Task Inventory
  "tasks": [
    {
      "name": string,
      "description": string,
      "frequency": string|null,  // daily, weekly, monthly, etc.
      "time_spent": string|null,  // "2 hours per day", "30 mins per week"
      "friction_level": string|null,  // "low", "medium", "high", "critical"
      "friction_points": [string],  // Specific bottlenecks, pain points
      "current_systems": [string],  // Tools/software currently used
      "manual_steps": [string]  // Repetitive manual actions described
    }
  ],
  
  // Additional context for STEP 3-5
  "business_goals": [string],  // Strategic objectives mentioned
  "existing_ai_initiatives": [string],  // Any AI/automation already in place
  "kpis_mentioned": [string],  // Metrics they care about
  "data_sources": [string],  // Databases, systems with data mentioned
  "data_quality_comments": [string],  // Any comments about data quality/availability
  "regulatory_concerns": [string],  // Compliance, privacy, regulatory issues mentioned
  "technical_constraints": [string],  // Technical limitations, legacy systems, etc.
  
  // What's still needed
  "missing": [string],
  "ready_for_report": boolean
}

Rules:
- No guessing; unknown => null/[].
- Task names should be verb+object (e.g., "Review customer invoices").
- friction_level: assess based on time wasted, repetition, error-proneness, user frustration
- friction_points: extract specific bottlenecks (e.g., "manual data entry", "waiting for approvals")
- manual_steps: look for repetitive actions (copy-paste, data entry, clicking through screens)

"missing" should be a checklist, e.g.:
["role", "detailed task breakdown", "friction severity", "time spent per task", "business goals", 
 "KPIs", "data availability", "regulatory constraints", "current systems"]

ready_for_report = true ONLY if you have:
- role, department
- >=5 granular tasks with descriptions
- friction_level for most tasks
- >=3 friction_points across tasks
- >=2 business_goals or kpis_mentioned
- Some information about data_sources or current_systems
- At least minimal feasibility context (data quality OR regulatory OR technical)
"""
    
    # Add company context if available
    if company_context:
        context_addition = "\n\n--- COMPANY CONTEXT (from previous interviews) ---\n"
        
        if company_context.get('north_star'):
            context_addition += f"\nNorth Star Strategy (already defined): {company_context['north_star']}\n"
            context_addition += "Note: You do NOT need to extract north_star_context again - it's already known.\n"
        
        if company_context.get('previous_tasks'):
            context_addition += f"\nPrevious tasks identified: {len(company_context['previous_tasks'])} tasks from other interviews\n"
            context_addition += "Look for: overlaps, dependencies, or gaps compared to what this person describes.\n"
        
        if company_context.get('previous_use_cases'):
            context_addition += f"\nPrevious AI use cases proposed: {len(company_context['previous_use_cases'])} opportunities\n"
            context_addition += "Note if this person's work relates to or validates existing use cases.\n"
        
        if company_context.get('interview_count'):
            context_addition += f"\nThis is interview #{company_context['interview_count'] + 1} for this company.\n"
        
        context_addition += "\n--- END COMPANY CONTEXT ---\n"
        base_prompt += context_addition
    
    return base_prompt


def build_question_planner_prompt(seniority_level: str, interview_count: int,
                                  should_ask_north_star: bool,
                                  company_context: Optional[Dict] = None) -> str:
    """
    Build role-aware question planning prompt
    
    Args:
        seniority_level: executive/senior/intermediate/junior/intern
        interview_count: Number of previous company interviews
        should_ask_north_star: Whether to ask about North Star
        company_context: Previous interviews data
    
    Returns:
        Customized system prompt for question planning
    """
    
    # Strategy based on seniority
    strategy_map = {
        "executive": "STRATEGIC - Focus on organization-wide goals, competitive pressures, budget priorities, transformation readiness",
        "senior": "TACTICAL - Focus on department goals, cross-functional processes, team capacity, implementation feasibility",
        "intermediate": "OPERATIONAL - Focus on specific processes, daily workflows, tools used, time spent on tasks",
        "junior": "TASK-LEVEL - Focus on daily tasks, repetitive work, tools, errors, handoffs",
        "intern": "TASK-LEVEL - Focus on tasks performed, time-consuming work, tools used, questions asked to colleagues"
    }
    
    strategy = strategy_map.get(seniority_level, strategy_map["intermediate"])
    
    base_prompt = f"""You are a professional AI interviewer conducting research for an AI Use Case Discovery framework.

INTERVIEW STRATEGY: {strategy}

SENIORITY LEVEL: {seniority_level.upper()}
INTERVIEW COUNT FOR COMPANY: {interview_count}
"""
    
    # Add company context awareness
    if interview_count > 0 and company_context:
        base_prompt += "\n--- COMPANY CONTEXT (from previous interviews) ---\n"
        
        if company_context.get('north_star'):
            base_prompt += f"\nNorth Star Strategy: {company_context['north_star']}\n"
            base_prompt += "DO NOT ask about North Star again - it's already defined.\n"
        
        if company_context.get('previous_tasks'):
            base_prompt += f"\n{len(company_context['previous_tasks'])} tasks already identified from other employees.\n"
            base_prompt += "Look for task overlaps, dependencies, or new perspectives on existing processes.\n"
        
        if company_context.get('previous_use_cases'):
            base_prompt += f"\n{len(company_context['previous_use_cases'])} AI use cases already proposed.\n"
            if seniority_level in ["executive", "senior", "intermediate"]:
                base_prompt += "AFTER gathering their tasks, show them relevant use cases and ask for feedback/validation.\n"
        
        base_prompt += "\n--- END COMPANY CONTEXT ---\n\n"
    
    # Role-specific question guidance
    base_prompt += f"""
Your goal is to gather information for 4 research steps:
STEP 2: Identify and decompose work into granular tasks (with friction points)
STEP 3: Understand business goals to align AI opportunities
STEP 4: Identify KPIs for measuring AI impact
STEP 5: Assess feasibility (data quality, regulatory risk, technical constraints)

Return ONLY valid JSON: {{"questions":[...]}}

Question Strategy for {seniority_level.upper()}:
"""
    
    # Seniority-specific strategy
    if seniority_level == "executive":
        base_prompt += """
1. FIRST (if no North Star): Ask about strategic priorities, competitive pressures, transformation goals
2. THEN: High-level process inefficiencies across departments
3. NEXT: Budget constraints, ROI expectations, change readiness
4. FINALLY: Organizational barriers to AI adoption

Avoid: Detailed task breakdowns (they delegate this work)
Focus: Strategic alignment, business value, organizational readiness
"""
    
    elif seniority_level == "senior":
        base_prompt += """
1. FIRST (if no North Star): Ask about department goals and how they ladder up to company strategy
2. THEN: Cross-functional processes they oversee, team bottlenecks
3. NEXT: Process standardization needs, resource constraints
4. FINALLY: Implementation concerns, data availability in their domain

Balance: Some strategic context + detailed process knowledge
Focus: Department-level optimization, team capacity, feasibility
"""
    
    elif seniority_level == "intermediate":
        base_prompt += """
1. FIRST: Ask about their role and main processes they manage
2. THEN: Step-by-step task breakdown, tools used, time spent
3. NEXT: Friction points - what's repetitive, manual, error-prone
4. FINALLY: Data quality, regulatory concerns, technical constraints

Focus: Detailed process knowledge, practical friction points, implementation reality
"""
    
    else:  # junior or intern
        base_prompt += """
1. FIRST: Ask about their daily tasks and responsibilities
2. THEN: What takes the most time, what's repetitive
3. NEXT: Tools they use, common errors or frustrations
4. FINALLY: What would make their work easier

Avoid: North Star questions (they don't know), strategic priorities, budget discussions
Focus: Granular task detail, practical pain points, hands-on tool usage
"""
    
    base_prompt += """

Rules for each question:
- Exactly ONE short question per item
- 1-2 sentences, max 40 words
- No lists, no numbering, no colons, no multiple questions, no bulletpoints
- Do NOT propose AI solutions yet - just gather information
- Use the "missing" list to decide what to ask next
- Be conversational and natural
- Adjust complexity based on seniority level

Generate 1-3 questions to systematically cover missing areas.
"""
    
    return base_prompt


def _extract_json_loose(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found.")
    return json.loads(text[start:end+1])

def update_notes(transcript: str, seniority_level: str = "intermediate", 
                 company_context: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Extract notes with role-awareness and company context
    
    Args:
        transcript: Full interview transcript
        seniority_level: Employee seniority level
        company_context: Previous company interviews data
    
    Returns:
        Extracted notes dictionary
    """
    c = _client()
    
    # Build context-aware prompt
    system_prompt = build_notes_extractor_prompt(seniority_level, company_context)
    
    resp = c.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": transcript},
        ],
    )
    return json.loads(resp.choices[0].message.content)

def plan_questions(notes: Dict[str, Any], history: List[Dict[str, str]],
                   seniority_level: str = "intermediate", interview_count: int = 0,
                   should_ask_north_star: bool = True,
                   company_context: Optional[Dict] = None) -> List[str]:
    """
    Plan questions with role-awareness and company context
    
    Args:
        notes: Current extracted notes
        history: Conversation history
        seniority_level: Employee seniority level
        interview_count: Number of previous company interviews
        should_ask_north_star: Whether to ask about North Star
        company_context: Previous interviews data
    
    Returns:
        List of questions to ask
    """
    c = _client()
    
    # Build context-aware prompt
    system_prompt = build_question_planner_prompt(
        seniority_level, interview_count, should_ask_north_star, company_context
    )
    
    # Keep recent turns so the planner can avoid repeating questions and follow context.
    history_window = [
        {"role": m.get("role"), "content": m.get("content", "")}
        for m in history[-12:]
        if m.get("role") in {"user", "assistant"} and m.get("content")
    ]
    planner_input = {
        "notes": notes,
        "history": history_window,
    }

    resp = c.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(planner_input)},
        ],
    )

    content = resp.choices[0].message.content
    try:
        data = json.loads(content)
    except Exception:
        data = _extract_json_loose(content)

    raw_questions = data.get("questions", [])
    if not isinstance(raw_questions, list):
        return []

    normalized: List[str] = []
    for item in raw_questions:
        if isinstance(item, str):
            q = item.strip()
            if q:
                normalized.append(q)
            continue
        if isinstance(item, dict):
            for key in ("question", "text", "content", "prompt"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    normalized.append(val.strip())
                    break

    return normalized

def next_question(history: List[Dict[str, str]], notes: Dict[str, Any],
                  seniority_level: str = "intermediate", interview_count: int = 0,
                  should_ask_north_star: bool = True, 
                  company_context: Optional[Dict] = None) -> str:
    """
    Get next question with role-awareness
    
    Args:
        history: Conversation history
        notes: Current extracted notes
        seniority_level: Employee seniority level
        interview_count: Number of previous company interviews
        should_ask_north_star: Whether to ask about North Star
        company_context: Previous interviews data
    
    Returns:
        Next question to ask
    """
    questions = plan_questions(
        notes, history, seniority_level, interview_count,
        should_ask_north_star, company_context
    )
    
    if questions:
        first = questions[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    
    # Fallback if no questions generated but not ready for report yet
    return "Could you tell me more about your process?"
