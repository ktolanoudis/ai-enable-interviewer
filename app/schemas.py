from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum

class FrictionLevel(str, Enum):
    """Friction/bottleneck severity"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class DataQuality(str, Enum):
    """Data availability and quality for AI implementation"""
    EXCELLENT = "excellent"  # Structured, clean, abundant
    GOOD = "good"  # Available but needs some cleaning
    FAIR = "fair"  # Exists but fragmented/incomplete
    POOR = "poor"  # Limited or very messy data
    NONE = "none"  # No data available

class RegulatoryRisk(str, Enum):
    """Regulatory and compliance risk level"""
    LOW = "low"  # Minimal regulatory concerns
    MEDIUM = "medium"  # Some compliance requirements
    HIGH = "high"  # Significant regulatory oversight
    CRITICAL = "critical"  # Heavily regulated (e.g., GDPR, HIPAA)

# STEP 2: Task Identification and Breakdown
class Task(BaseModel):
    """Granular task from employee's day-to-day work"""
    name: str = Field(description="Short task name")
    description: str = Field(description="Detailed description of the task")
    department: str = Field(description="Department where task is performed")
    frequency: str = Field(description="How often: daily, weekly, monthly, quarterly")
    time_spent: str = Field(description="Average time spent per occurrence")
    friction_level: FrictionLevel = Field(description="Severity of bottleneck/friction")
    friction_points: List[str] = Field(default=[], description="Specific pain points")
    current_systems: List[str] = Field(default=[], description="Current tools/systems used")
    manual_steps: List[str] = Field(default=[], description="Manual/repetitive steps involved")

# STEP 3: AI Use Case Identification
class UseCase(BaseModel):
    """Proposed AI solution matched to task"""
    task_name: str = Field(description="Reference to Task.name")
    use_case_name: str = Field(description="Name of the AI use case")
    ai_solution_type: str = Field(description="Type of AI: LLM, Computer Vision, ML, RPA, etc.")
    description: str = Field(description="How AI addresses the task/friction")
    business_alignment: str = Field(description="How this aligns with North Star strategy")
    
    # STEP 4: KPI Definition
    kpis: List[str] = Field(description="Measurable KPIs (time saved, cost reduction, accuracy, etc.)")
    expected_impact: str = Field(description="Quantified expected improvement")
    
    # STEP 5: Feasibility Evaluation
    data_quality: DataQuality = Field(description="Quality/availability of data needed")
    data_requirements: str = Field(description="Specific data needed for AI implementation")
    regulatory_risk: RegulatoryRisk = Field(description="Regulatory/compliance risk level")
    regulatory_concerns: List[str] = Field(default=[], description="Specific compliance issues")
    technical_feasibility: str = Field(description="Technical complexity: low/medium/high")
    implementation_effort: str = Field(description="Estimated effort: weeks/months/quarters")
    
    # Value-Feasibility Scorecard
    value_score: Optional[int] = Field(default=None, ge=1, le=10, description="Business value score (1-10)")
    feasibility_score: Optional[int] = Field(default=None, ge=1, le=10, description="Implementation feasibility (1-10)")
    priority_quadrant: Optional[str] = Field(default=None, description="Quick Win / Strategic / Fill-In / Hard Slog")

class Report(BaseModel):
    """Complete AI Use Case Discovery Report following the framework"""
    # Executive Summary
    executive_summary: str = Field(description="High-level overview of findings")
    north_star_alignment: str = Field(description="How findings align with organization's North Star strategy")
    north_star_source: str = Field(
        description="North Star provenance: senior_stakeholder_interview, inferred_from_online_research, existing_company_memory, or not_specified"
    )
    
    # Step 2 Output: Task Inventory
    tasks: List[Task] = Field(description="Structured task inventory from interviews")
    total_friction_points: int = Field(description="Total high/critical friction tasks identified")
    
    # Step 3 Output: Candidate AI Use Cases
    use_cases: List[UseCase] = Field(description="Proposed AI use cases matched to tasks")
    
    # Step 4-5 Summary: Prioritization
    priority_recommendations: List[str] = Field(description="Prioritized use cases based on value-feasibility matrix")
    quick_wins: List[str] = Field(default=[], description="High value, low effort use cases")
    strategic_initiatives: List[str] = Field(default=[], description="High value, high effort use cases")
    
    # Risk Assessment
    key_risks: List[str] = Field(default=[], description="Major regulatory/technical risks identified")
    mitigation_strategies: List[str] = Field(default=[], description="Recommended risk mitigation approaches")
    
    # Next Steps
    recommended_next_steps: List[str] = Field(description="Concrete next actions for implementation")
