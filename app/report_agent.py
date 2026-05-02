import os, json
from dotenv import load_dotenv
from openai import OpenAI
try:
    from .schemas import Report
except ImportError:
    from schemas import Report

load_dotenv(override=False)

MODEL = os.getenv("OPENAI_MODEL_REPORT", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY missing. Put it in .env.")

    base_url = os.getenv("OPENAI_BASE_URL")  # optional for LiteLLM proxy
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)

SYSTEM_PROMPT = """
You are generating an AI Use Case Discovery Report following an academic research framework.

The framework has 4 steps that you must complete:

STEP 2: Task Identification and Breakdown
- Decompose employee's work into granular, simple tasks
- Identify friction levels and bottlenecks for each task
- Document time spent, frequency, current systems, and manual steps

STEP 3: AI Use Case Identification  
- Match each high-friction task with a candidate AI solution
- Ensure alignment with business goals ("North Star" strategy)
- Specify AI solution type (LLM, Computer Vision, ML, RPA, etc.)

STEP 4: KPI Definition
- Define measurable KPIs for each use case (time saved, cost reduction, accuracy improvement, etc.)
- Quantify expected impact where possible

STEP 5: Feasibility Evaluation
- Assess data quality and availability (excellent/good/fair/poor/none)
- Identify regulatory risks (low/medium/high/critical)
- Evaluate technical feasibility and implementation effort
- Assign value score (1-10) and feasibility score (1-10)
- Categorize into priority quadrants: "Quick Win" (high value, high feasibility), "Strategic" (high value, low feasibility), "Fill-In" (low value, high feasibility), "Hard Slog" (low value, low feasibility)

Return ONLY valid JSON matching this schema:
{
  "executive_summary": string,
  "north_star_alignment": string,
  "north_star_source": string,  // "senior_stakeholder_interview" | "inferred_from_online_research" | "existing_company_memory" | "not_specified"
  "tasks": [
    {
      "name": string,
      "description": string,
      "department": string,
      "frequency": string,  // "daily", "weekly", "monthly", etc.
      "time_spent": string,  // "2 hours/day", "30 mins/week"
      "friction_level": string,  // "low", "medium", "high", "critical"
      "friction_points": [string],
      "current_systems": [string],
      "manual_steps": [string]
    }
  ],
  "total_friction_points": integer,
  "use_cases": [
    {
      "task_name": string,
      "use_case_name": string,
      "ai_solution_type": string,  // "LLM", "Computer Vision", "RPA", "Predictive ML", etc.
      "description": string,
      "business_alignment": string,
      "kpis": [string],
      "expected_impact": string,  // Quantified impact like "Save 10 hours/week" or "Reduce errors by 50%"
      "data_quality": string,  // "excellent", "good", "fair", "poor", "none"
      "data_requirements": string,
      "regulatory_risk": string,  // "low", "medium", "high", "critical"
      "regulatory_concerns": [string],
      "technical_feasibility": string,  // "low", "medium", "high" complexity
      "implementation_effort": string,  // "2-4 weeks", "1-3 months", "3-6 months", etc.
      "value_score": integer,  // 1-10
      "feasibility_score": integer,  // 1-10
      "priority_quadrant": string  // "Quick Win", "Strategic", "Fill-In", "Hard Slog"
    }
  ],
  "priority_recommendations": [string],
  "quick_wins": [string],
  "strategic_initiatives": [string],
  "key_risks": [string],
  "mitigation_strategies": [string],
  "recommended_next_steps": [string]
}

Critical Rules:
- Base EVERYTHING on the transcript - do not invent information
- If data is missing for required string fields, use "Not specified" (not null)
- Use interview_metadata.north_star_source_hint to set north_star_source whenever available.
- If north_star_source_hint is absent or unclear, use "not_specified".
- For optional/list fields, use [] where appropriate
- Tasks should be granular and actionable (verb + object)
- Friction levels should be assessed based on time waste, repetition, error-proneness
- AI solution types should be specific and realistic
- KPIs must be measurable and quantifiable
- Value scores reflect business impact; feasibility scores reflect ease of implementation
- Priority quadrants:
  * Quick Win: value_score >= 7 AND feasibility_score >= 7
  * Strategic: value_score >= 7 AND feasibility_score < 7
  * Fill-In: value_score < 7 AND feasibility_score >= 7  
  * Hard Slog: value_score < 7 AND feasibility_score < 7
- Focus on use cases where AI transforms well-defined, repetitive tasks
- Prioritize high-friction tasks that align with business goals
- Do NOT present AI/automation that the employee says already exists, is already used, or is currently being implemented as a new proposed opportunity.
- If an existing or in-flight AI capability is relevant, mention it only as a current system, existing AI initiative, risk, or next-step optimization; propose only the unmet gap around it.
- If there is no clear unmet gap beyond the existing/in-flight capability, exclude that candidate from use_cases.
"""

def _extract_json_loose(text: str) -> dict:
    """
    Fallback if the model returns extra text.
    Tries to grab the first {...} block.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output.")
    return json.loads(text[start : end + 1])


def _sanitize_report_data(data: dict) -> dict:
    """
    Normalize model JSON so Pydantic validation doesn't fail on nulls.
    """
    if not isinstance(data, dict):
        return data

    # Top-level required string fields
    for key in ("executive_summary", "north_star_alignment", "north_star_source"):
        if data.get(key) is None:
            data[key] = "Not specified"

    if data.get("north_star_source") not in {
        "senior_stakeholder_interview",
        "inferred_from_online_research",
        "existing_company_memory",
        "not_specified",
    }:
        data["north_star_source"] = "not_specified"

    # Top-level list fields
    for key in (
        "tasks",
        "use_cases",
        "priority_recommendations",
        "quick_wins",
        "strategic_initiatives",
        "key_risks",
        "mitigation_strategies",
        "recommended_next_steps",
    ):
        if data.get(key) is None:
            data[key] = []

    # Numeric fallback
    if data.get("total_friction_points") is None:
        data["total_friction_points"] = 0

    def _normalize_enum(value, allowed: set[str], default: str) -> str:
        if value is None:
            return default
        v = str(value).strip().lower()
        return v if v in allowed else default

    for task in data.get("tasks", []):
        if not isinstance(task, dict):
            continue
        for key in ("name", "description", "department", "frequency", "time_spent"):
            if task.get(key) is None:
                task[key] = "Not specified"
        task["friction_level"] = _normalize_enum(
            task.get("friction_level"),
            {"low", "medium", "high", "critical"},
            "medium",
        )
        for key in ("friction_points", "current_systems", "manual_steps"):
            if task.get(key) is None:
                task[key] = []

    for uc in data.get("use_cases", []):
        if not isinstance(uc, dict):
            continue
        for key in (
            "task_name",
            "use_case_name",
            "ai_solution_type",
            "description",
            "business_alignment",
            "expected_impact",
            "data_requirements",
            "technical_feasibility",
            "implementation_effort",
        ):
            if uc.get(key) is None:
                uc[key] = "Not specified"
        uc["data_quality"] = _normalize_enum(
            uc.get("data_quality"),
            {"excellent", "good", "fair", "poor", "none"},
            "fair",
        )
        uc["regulatory_risk"] = _normalize_enum(
            uc.get("regulatory_risk"),
            {"low", "medium", "high", "critical"},
            "medium",
        )
        for key in ("kpis", "regulatory_concerns"):
            if uc.get(key) is None:
                uc[key] = []

    return data

def generate_report(transcript: str) -> Report:
    client = _client()

    # Try to force JSON output (works with OpenAI; many LiteLLM proxies also support it)
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        data = _sanitize_report_data(json.loads(resp.choices[0].message.content))
        return Report(**data)
    except Exception:
        # Fallback: attempt loose extraction
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            temperature=0,
        )
        data = _sanitize_report_data(_extract_json_loose(resp.choices[0].message.content))
        return Report(**data)
