# AI Use Case Discovery Agent

> **Multi-stakeholder conversational AI system for discovering and prioritizing AI opportunities in organizations**

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Chainlit](https://img.shields.io/badge/framework-Chainlit-orange.svg)](https://docs.chainlit.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Overview

This is a **research-grade AI interview system** that conducts structured discovery interviews with multiple stakeholders across an organization to identify, validate, and prioritize AI opportunities.

### What Makes It Unique?

- **Organizational Memory**: Remembers all previous interviews from the same company
- **Role-Aware Intelligence**: Adapts questions based on seniority (CEO vs. Intern)
- **Automatic Company Research**: Looks up companies online to provide context
- **Use Case Validation**: Cross-stakeholder feedback on proposed AI solutions
- **Academic Framework**: Implements research-based Steps 2-5 methodology
- **Conversational Intelligence**: Handles meta-questions, corrections, and uncertainty

### Built For

- **Researchers**: Thesis-ready data collection with structured outputs
- **Consultants**: Rapid AI opportunity assessment for clients
- **Organizations**: Internal AI transformation discovery
- **Investors**: Due diligence on AI readiness

---

## Quick Start

### Prerequisites

- Python 3.13+
- OpenAI API key (or LiteLLM proxy access)

### Installation

```bash
# 1. Clone the repository
git clone <your-repo-url>
cd discovery

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
```

### Run the App

```bash
chainlit run app/chainlit_app.py -w --port "${PORT:-8000}"
```

Then open your browser to `http://localhost:${PORT:-8000}`

---

## Features

### 1. Multi-Stakeholder Discovery

Interview **10+ people from the same organization** and watch the system build progressive knowledge:

- **Interview #1 (CEO)**: Defines North Star strategy
- **Interview #2 (Manager)**: Sees CEO's North Star, doesn't get asked again
- **Interview #3 (Analyst)**: Validates use cases from previous interviews
- **Interview #10 (Intern)**: Benefits from 42 tasks and 15 AI opportunities already identified

### 2. Role-Aware Interviewing

Automatically detects seniority and adapts:

| Seniority | Strategy | Example Questions |
|-----------|----------|-------------------|
| **Executive** | Strategic | "What are your strategic priorities?" |
| **Senior** | Tactical | "What department-level bottlenecks exist?" |
| **Intermediate** | Operational | "Walk me through your daily workflow" |
| **Junior/Intern** | Task-level | "What tasks do you do every day?" |

### 3. Automatic Company Research

When a user enters their company name:

1. Agent automatically searches online (DuckDuckGo, SerpAPI, OpenAI)
2. Displays company description for confirmation
3. User can confirm or correct if wrong
4. Stored for all subsequent interviews

### 4. Academic Framework (Steps 2-5)

Implements research-based methodology:

- **STEP 2**: Task Identification & Breakdown (friction analysis)
- **STEP 3**: AI Use Case Discovery (aligned with business goals)
- **STEP 4**: KPI Definition (measurable success metrics)
- **STEP 5**: Feasibility Evaluation (data, regulatory, technical)

### 5. Value-Feasibility Scorecard

Each AI opportunity is scored and categorized:

- **Quick Wins**: High value + High feasibility → Implement first!
- **Strategic**: High value + Low feasibility → Plan carefully
- **Fill-In**: Low value + High feasibility → Nice-to-have
- **Hard Slog**: Low value + Low feasibility → Avoid

---

## Output

Every interview generates:

### 1. JSON Report
```json
{
  "executive_summary": "...",
  "north_star_alignment": "...",
  "tasks": [
    {
      "name": "Review customer invoices",
      "friction_level": "high",
      "time_spent": "10 hours/week",
      "friction_points": ["manual data entry", "cross-system lookup"]
    }
  ],
  "use_cases": [
    {
      "use_case_name": "Automated Invoice Processing",
      "value_score": 9,
      "feasibility_score": 7,
      "priority_quadrant": "Quick Win"
    }
  ],
  "quick_wins": [...],
  "recommended_next_steps": [...]
}
```

### 2. Markdown Report
Human-readable report with:
- Executive summary
- Task inventory with friction analysis
- AI use case recommendations
- Prioritized action items

### 3. Persistence and Storage
- Interview/session data is stored in **SQLite** by default, or **MongoDB** when `DB_BACKEND=mongodb`.
- Report files are written to local disk by default (`LOCAL_REPORTS_DIR`, default: `reports/`).
- Optional S3-compatible upload is available with `REPORT_STORAGE_BACKEND=s3`.
- Set `DISABLE_LOCAL_REPORTS=1` only if you want report files to be remote-only.

---

## Configuration

### Environment Variables (.env)

```bash
# Required
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_MODEL_REPORT=gpt-4o-mini

# Optional - LiteLLM Proxy
OPENAI_BASE_URL=https://litellm.sph-prod.ethz.ch/v1

# Optional - Enhanced Company Research
SERPAPI_KEY=your_serpapi_key  # For better company lookup

# Optional - Enable debug logs for question flow
DEBUG_QUESTION_FLOW=1

# Optional - App port for local/dev runs
PORT=8000

# Optional - Stateless DB backend (recommended for server deployment)
DB_BACKEND=mongodb
MONGODB_URI=mongodb+srv://<user>:<pass>@<cluster>/<db>?retryWrites=true&w=majority
MONGODB_DB_NAME=ai_enable_discovery

# Optional - Stateless report storage (Backblaze B2 S3-compatible)
REPORT_STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://s3.<region>.backblazeb2.com
S3_REGION=us-east-005
S3_BUCKET=your-bucket-name
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
REPORTS_PREFIX=reports
# Optional local path for report files (used unless DISABLE_LOCAL_REPORTS=1)
LOCAL_REPORTS_DIR=reports
# Optional public URL base for links shown in completion message
S3_PUBLIC_BASE_URL=https://f005.backblazeb2.com/file/your-bucket-name
# Set to 1 to avoid writing local report files in container/VM
DISABLE_LOCAL_REPORTS=1
```

### Chainlit Config (.chainlit/config.toml)

```toml
[project]
enable_telemetry = false

[features]
unsafe_allow_html = true  # Required for CSS injection

[UI]
name = "AI Use Case Discovery"
custom_css = "/public/custom.css"
```

---

## Docker Deployment

This project supports both deployment patterns:
- **Stateful (default):** local SQLite + local report files (`/app/data`, `/app/reports`)
- **Stateless:** MongoDB + S3-compatible report storage (`DISABLE_LOCAL_REPORTS=1`)
- Containerized app runtime

```bash
# Build
docker build -t ai-enable-discovery:latest .

# Run
docker run --rm -p 8000:8000 \
  --env-file .env \
  -e SQLITE_DB_PATH=/app/data/sessions.db \
  -e LOCAL_REPORTS_DIR=/app/reports \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/reports:/app/reports" \
  ai-enable-discovery:latest
```

Or use the helper script:

```bash
chmod +x scripts/docker-run.sh
./scripts/docker-run.sh
```

Notes:
- For stateless mode, set `DB_BACKEND=mongodb`, `REPORT_STORAGE_BACKEND=s3`, and `DISABLE_LOCAL_REPORTS=1`.
- If these vars are omitted, the app defaults to local SQLite + local report files.

### Auto-Update From GitHub

This repo includes:
- `.github/workflows/docker-publish.yml`: builds and pushes image to GHCR on every push to `main`
- `docker-compose.yml`: runs app + Watchtower for automatic image pull/restart

Server setup:

```bash
# 1) On your server, set the image you want to track
export DISCOVERY_IMAGE=ghcr.io/<your-user-or-org>/<your-repo>:latest

# 2) (If package is private) login once
echo <github_pat_with_read_packages> | docker login ghcr.io -u <github_username> --password-stdin

# 3) Start stack
docker compose up -d
```

After that, each new push to `main` builds a fresh image and Watchtower updates the running container automatically.

---

## Testing

### Quick Test: Single Interview

```bash
chainlit run app/chainlit_app.py -w --port "${PORT:-8000}"

# Interview flow:
1. Enter name: John Smith
2. Department: Finance
3. Role: Financial Analyst
4. Company: Acme Corp
   → Agent researches company automatically
   → Shows description, asks for confirmation
5. Answer interview questions naturally
6. Receive comprehensive report
```

### Full Test: Multi-Stakeholder

```bash
# Interview 4 people from "Test Corp":

# Person 1: CEO
→ Asked about North Star
→ Strategic questions

# Person 2: Operations Manager  
→ Shows CEO's North Star
→ NOT asked about North Star again
→ Shows use cases for validation

# Person 3: Data Analyst
→ Shows accumulated context (2 previous interviews)
→ Detailed task questions

# Person 4: Intern
→ Shows 15+ tasks already identified
→ NO strategic questions
→ NO use case validation (lacks context)
```

### Database Query Test

```python
from app.db import get_company_sessions, get_company_insights

# Check all interviews
sessions = get_company_sessions("Test Corp")
print(f"Total interviews: {len(sessions)}")

# Check aggregated insights
insights = get_company_insights("Test Corp")
print(f"North Star: {insights['north_star']}")
print(f"Tasks: {len(insights['all_tasks'])}")
print(f"Use Cases: {len(insights['all_use_cases'])}")
```

---

## Use Cases

### 1. Academic Research
- **Thesis data collection** with structured framework
- **Cross-company analysis** of AI readiness
- **Seniority-based insights** (executive vs. employee perspectives)
- **Validation studies** on AI opportunity discovery methods

### 2. Consulting
- **Rapid client assessment** (5-10 stakeholder interviews)
- **Multi-department discovery** in 1-2 days
- **Prioritized recommendations** with ROI estimates
- **Data-driven proposals** backed by stakeholder validation

### 3. Internal Transformation
- **Organizational AI readiness** assessment
- **Bottleneck identification** across departments
- **Change management insights** from all levels
- **Implementation roadmap** based on feasibility

### 4. Investment Due Diligence
- **Portfolio company assessment** of AI potential
- **Operational efficiency analysis** via employee interviews
- **Risk identification** (regulatory, technical, data)
- **Value creation opportunities** with quantified impact

---

## Advanced Configuration

### Disable Company Research

Edit `app/chainlit_app.py`:

```python
# Comment out:
# company_info = research_company(metadata["company"], use_ai=True)

# Replace with:
company_info = {'name': metadata["company"], 'description': None, 'source': None}
```

### Change Seniority Keywords

Edit `app/role_classifier.py`:

```python
executive_keywords = [
    'ceo', 'cto', 'cfo',
    'your_custom_title'  # Add custom titles
]
```

### Customize Interview Questions

Edit `app/interview_agent.py`, functions:
- `build_notes_extractor_prompt()` - What to extract
- `build_question_planner_prompt()` - Question strategy

### Use Different LLM

Edit `.env`:

```bash
OPENAI_MODEL=gpt-4-turbo
OPENAI_MODEL_REPORT=gpt-4o  # Use different model for reports
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Contact

**For questions, suggestions, or collaboration:**

- Email: [ktolanoudis@ethz.ch]

---
