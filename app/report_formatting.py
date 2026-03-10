import datetime

from schemas import Report


def generate_markdown_report(report: Report, metadata: dict) -> str:
    """Convert Report object to Markdown following thesis framework structure."""
    md = "# AI Use Case Discovery Report\n\n"
    md += "## Metadata\n\n"
    md += f"- **Employee:** {metadata['employee_name']}\n"
    md += f"- **Department:** {metadata['department']}\n"
    md += f"- **Position:** {metadata['role']}\n"
    md += f"- **Seniority:** {metadata.get('seniority_level', 'N/A')}\n"
    md += f"- **Company:** {metadata['company']}\n"
    md += f"- **Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"

    md += "---\n\n"
    md += f"## Executive Summary\n\n{report.executive_summary}\n\n"
    md += f"### North Star Alignment\n\n{report.north_star_alignment}\n\n"

    md += "---\n\n"
    md += f"## STEP 2: Task Inventory ({len(report.tasks)} tasks identified)\n\n"
    md += f"**Total High-Friction Points:** {report.total_friction_points}\n\n"

    for i, task in enumerate(report.tasks, 1):
        md += f"### Task {i}: {task.name}\n\n"
        md += f"- **Department:** {task.department}\n"
        md += f"- **Description:** {task.description}\n"
        md += f"- **Frequency:** {task.frequency}\n"
        md += f"- **Time Spent:** {task.time_spent}\n"
        md += f"- **Friction Level:** {task.friction_level.upper()}\n"
        if task.friction_points:
            md += "- **Friction Points:**\n"
            for fp in task.friction_points:
                md += f"  - {fp}\n"
        if task.current_systems:
            md += f"- **Current Systems:** {', '.join(task.current_systems)}\n"
        if task.manual_steps:
            md += "- **Manual Steps:**\n"
            for step in task.manual_steps:
                md += f"  - {step}\n"
        md += "\n"

    md += "---\n\n"
    md += f"## STEP 3-5: AI Use Case Analysis ({len(report.use_cases)} opportunities)\n\n"

    for i, uc in enumerate(report.use_cases, 1):
        md += f"### Use Case {i}: {uc.use_case_name}\n\n"
        md += f"**Related Task:** {uc.task_name}\n\n"

        md += "#### Step 3: Solution Design\n"
        md += f"- **AI Solution Type:** {uc.ai_solution_type}\n"
        md += f"- **Description:** {uc.description}\n"
        md += f"- **Business Alignment:** {uc.business_alignment}\n\n"

        md += "#### Step 4: KPIs & Impact\n"
        md += f"- **Expected Impact:** {uc.expected_impact}\n"
        md += "- **KPIs:**\n"
        for kpi in uc.kpis:
            md += f"  - {kpi}\n"
        md += "\n"

        md += "#### Step 5: Feasibility Assessment\n"
        md += f"- **Data Quality:** {uc.data_quality.upper()}\n"
        md += f"- **Data Requirements:** {uc.data_requirements}\n"
        md += f"- **Regulatory Risk:** {uc.regulatory_risk.upper()}\n"
        if uc.regulatory_concerns:
            md += f"- **Regulatory Concerns:** {', '.join(uc.regulatory_concerns)}\n"
        md += f"- **Technical Feasibility:** {uc.technical_feasibility}\n"
        md += f"- **Implementation Effort:** {uc.implementation_effort}\n\n"

        md += "#### Value-Feasibility Scorecard\n"
        md += f"- **Value Score:** {uc.value_score}/10\n"
        md += f"- **Feasibility Score:** {uc.feasibility_score}/10\n"
        md += f"- **Priority:** **{uc.priority_quadrant}**\n\n"
        md += "---\n\n"

    md += "## Prioritization & Recommendations\n\n"

    if report.quick_wins:
        md += "### Quick Wins (High Value, High Feasibility)\n\n"
        for qw in report.quick_wins:
            md += f"- {qw}\n"
        md += "\n"

    if report.strategic_initiatives:
        md += "### Strategic Initiatives (High Value, Lower Feasibility)\n\n"
        for si in report.strategic_initiatives:
            md += f"- {si}\n"
        md += "\n"

    md += "### Overall Recommendations\n\n"
    for rec in report.priority_recommendations:
        md += f"- {rec}\n"
    md += "\n"

    md += "## Risk Management\n\n"
    if report.key_risks:
        md += "### Key Risks Identified\n\n"
        for risk in report.key_risks:
            md += f"- {risk}\n"
        md += "\n"

    if report.mitigation_strategies:
        md += "### Mitigation Strategies\n\n"
        for strategy in report.mitigation_strategies:
            md += f"- {strategy}\n"
        md += "\n"

    md += "## Next Steps\n\n"
    for step in report.recommended_next_steps:
        md += f"- {step}\n"

    return md
