# AI-Enable: AI Use Case Discovery Agent

A conversational app for discovering, validating, and prioritizing internal AI opportunities through structured employee interviews.

The app is built around the AI use-case discovery framework used in the thesis work behind this project. It focuses on Steps 2 to 5:

- Step 2: identify day-to-day tasks and workflow details
- Step 3: generate AI use case opportunities
- Step 4: define useful KPIs
- Step 5: assess feasibility, constraints, and implementation risk

It interviews employees, builds company-level memory across interviews, generates structured reports, and asks interviewees to review the proposed AI use cases before the interview is closed.

---

# Quick Start

Requirements

- Python 3.13+
- OpenAI API key
- MongoDB Atlas recommended for shared drafts and production use

Clone the repository

```bash
git clone https://github.com/ktolanoudis/ai-enable-interviewer.git
cd ai-enable-interviewer
```

Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate
```

Install dependencies

```bash
pip install -r requirements.txt
```

Create the environment file

```bash
cp .env.example .env
```

Set the required values in `.env`, especially:

```bash
OPENAI_API_KEY=sk-...
DB_BACKEND=mongodb
MONGODB_URI=...
MONGODB_DB_NAME=ai_enable_discovery
```

Run locally without Docker

```bash
chainlit run app/chainlit_app.py --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000
```

---

# Docker Run

The main Docker entrypoint for local and server use is:

```bash
./scripts/docker-run.sh
```

What it does:

- builds the image from the current source
- removes the previous `discovery-app` container
- starts the app on the configured host port
- loads runtime configuration from `.env`

If `DB_BACKEND=mongodb` and `MONGODB_URI` are set, the app uses MongoDB Atlas.

If the app is configured for local SQLite instead, the script mounts:

- `data/` to `/app/data`
- `reports/` to `/app/reports`

Default local URL:

```text
http://localhost:8000
```

Ports can be overridden in `.env`:

```bash
PORT=8000
HOST_PORT=8000
```

---

# What The App Does

The app runs a structured chatbot interview without turning the experience into a form.

The fixed setup flow collects:

- name
- company
- company website
- work email
- department
- role

The app then researches the company website and public context, summarizes what it found, and asks the interviewee to confirm or correct it.

The main interview focuses on:

- day-to-day tasks
- friction points and bottlenecks
- systems, tools, and data sources
- business goals and KPIs
- constraints around data quality, regulation, explainability, and implementation

Questioning is primarily LLM-planned from the current transcript and extracted notes. Deterministic logic is used for readiness checks, close conditions, draft handling, skip handling, and recurring-theme validation, but normal follow-up questions are not chosen through a rigid task-by-task objective selector.

---

# Interview Drafts

Drafts allow users to refresh or reconnect without losing an unfinished interview.

Draft behavior:

- in-progress interviews are checkpointed to persistent storage
- drafts are scoped to the browser or authenticated user identity
- refresh and reconnect restore the active unfinished interview
- completed interviews delete their draft checkpoints
- New Chat abandons the open unfinished draft for that client identity and starts clean

This keeps normal resume behavior while preventing old drafts from reappearing after the user intentionally starts over.

---

# Company Memory

Completed interviews update company-level memory.

Company memory includes:

- recurring tasks and workflow patterns
- proposed AI use cases
- employee feedback on generated use cases
- recurring themes inferred from interview content
- contradictions or validations of previously observed themes

Recurring themes are inferred dynamically from completed interviews. Similar themes are merged within the same company, and later interviews only validate themes that appear relevant to the current interviewee’s role, department, tasks, or systems.

MongoDB and SQLite updates are protected against common read-merge-write issues. MongoDB uses company insight locks, while SQLite uses transactional writes for company insight updates.

---

# Use Case Review

After the interview, the app generates AI use cases and asks the interviewee to review them.

For each use case, the app can collect:

- a short free-text reaction
- a usefulness rating from `1` to `5`
- scope feedback if the use case belongs to another role or team
- feasibility comments and ratings when relevant

If the interviewee skips a use case, the app skips the whole use-case review item and moves on.

Feasibility review is use-case-specific. Depending on the user’s role and the use case, the app may ask about:

- data quality and availability
- regulatory or compliance risk
- explainability or auditability

Those feasibility prompts collect a short comment first, then a rating or risk label.

---

# Survey Handoff

The app can send the user to an external post-interview questionnaire after the report is generated.

Configure the survey URL with:

```bash
POST_INTERVIEW_SURVEY_URL=https://www.soscisurvey.de/your-project/
```

When a survey URL is configured, the app appends the internal contributor key as:

```text
r=<contributor_key>
```

For SoSci Survey, this value appears in the exported `REF` field. It can be joined back to:

```text
sessions.contributor_key
```

This allows the external questionnaire response to be linked to the interview session without exposing the user’s raw email address.

---

# Unknown Tools And Terms

The app does not assume every tool, acronym, or product name is already understood.

If an interviewee mentions an important named tool or term, the app can:

- look for public context online
- ask the interviewee to confirm whether the public context is the right one
- ask for a short explanation if the term appears internal or ambiguous
- store the clarified meaning for the rest of the interview and report

This is useful for internal systems, niche tools, abbreviations, and product names that should not be guessed from model pretraining alone.

---

# Outputs

Each completed interview produces:

- a structured JSON report
- a Markdown report
- a persisted session record
- company-level insight updates

Reports include:

- executive summary
- task inventory
- proposed AI use cases
- KPI suggestions
- feasibility assessment
- value-feasibility prioritization
- employee feedback on proposed use cases

---

# Storage

Database backends:

- MongoDB Atlas for production and shared deployments
- SQLite for local-only fallback setups

Report storage backends:

- S3-compatible object storage
- local filesystem fallback

Runtime configuration is controlled through `.env`.

---

# Project Structure

Important files and modules:

- [app/chainlit_app.py](/root/discovery/app/chainlit_app.py)
  Chainlit entrypoint, runtime event handling, resume behavior, and message routing
- [app/company_flow.py](/root/discovery/app/company_flow.py)
  metadata collection and company-context confirmation flow
- [app/question_flow.py](/root/discovery/app/question_flow.py)
  notes updates, readiness checks, company-theme validation, and LLM question planning
- [app/interview_agent.py](/root/discovery/app/interview_agent.py)
  notes extraction and next-question planning prompts
- [app/interview_flow.py](/root/discovery/app/interview_flow.py)
  closeout flow, final review handling, and use-case feedback state machine
- [app/feedback_flow.py](/root/discovery/app/feedback_flow.py)
  report finalization, feedback aggregation, session persistence, and survey handoff
- [app/company_memory.py](/root/discovery/app/company_memory.py)
  recurring-theme extraction, relevance checks, and theme alignment
- [app/term_discovery.py](/root/discovery/app/term_discovery.py)
  public lookup and clarification for unknown tools and terms
- [app/company_research.py](/root/discovery/app/company_research.py)
  company website and public web research
- [app/db.py](/root/discovery/app/db.py)
  MongoDB and SQLite persistence
- [app/checkpoints.py](/root/discovery/app/checkpoints.py)
  draft checkpoint save, restore, ownership, and fallback lookup
- [public/branding.js](/root/discovery/public/branding.js)
  browser branding, progress bar behavior, New Chat handling, and survey link behavior
- [public/custom.css](/root/discovery/public/custom.css)
  UI styling and responsive layout adjustments
- [scripts/docker-run.sh](/root/discovery/scripts/docker-run.sh)
  build-and-run script used for Docker-based runs

---

# Environment Notes

The provided `.env.example` covers:

- OpenAI model settings
- MongoDB Atlas settings
- SQLite fallback settings
- S3-compatible report storage
- local report storage fallback
- optional SerpAPI support for public lookup
- optional post-interview survey URL

For server use, MongoDB Atlas and S3-compatible report storage are the intended default path.

---

# Contact

Konstantinos Tolanoudis  
ETH Zurich

ktolanoudis@ethz.ch
