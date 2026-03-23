# AI-Enable: AI Use Case Discovery Agent

A conversational system for identifying, evaluating, and prioritizing AI opportunities inside organizations through structured employee interviews.

The app covers Steps 2 to 5 of the framework used in the thesis work behind the project:

- Step 2: task identification and breakdown
- Step 3: AI use case discovery
- Step 4: KPI definition
- Step 5: feasibility assessment

It conducts role-aware interviews, accumulates organizational context across interviews, produces structured reports, and collects employee feedback on proposed AI use cases at the end of the interview.

---

# Quick Start

Requirements

- Python 3.13+
- OpenAI API key
- MongoDB Atlas recommended for shared interview drafts and production use

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

Then set the required values in `.env`, especially:

```bash
OPENAI_API_KEY=sk-...
DB_BACKEND=mongodb
MONGODB_URI=...
MONGODB_DB_NAME=ai_enable_discovery
```

Run the app locally without Docker

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

- builds the image from source
- removes any previous `discovery-app` container
- starts the app container on the configured port
- uses `.env` for runtime configuration

If `DB_BACKEND=mongodb` and `MONGODB_URI` is set, the container runs against MongoDB Atlas.

If you switch to a local SQLite setup instead, the script mounts:

- `data/` to `/app/data`
- `reports/` to `/app/reports`

By default the app starts at:

```text
http://localhost:8000
```

You can override ports through `.env`:

```bash
PORT=8000
HOST_PORT=8000
```

---

# What The App Does

The interview flow is designed to gather enough operational detail to generate useful AI opportunities without turning the conversation into a form.

Core behavior:

- collects employee metadata in a fixed order:
  - name
  - company
  - company website
  - work email
  - department
  - role
- researches the company from its website and public sources, then asks the interviewee to confirm or correct that context
- adapts questions based on seniority and previous company interviews
- keeps draft progress in persistent storage so a browser refresh can restore the active interview
- reuses company memory from prior interviews to avoid starting from zero each time

The interview itself focuses on:

- day-to-day tasks
- friction points and bottlenecks
- tools and systems in use
- business goals and KPIs
- data, process, and implementation constraints

---

# Use Case Review

At the end of the interview, the app does not stop at generating use cases internally. It also asks the interviewee to review them.

For each proposed use case, the app can collect:

- a practical opinion in free text
- a usefulness rating from `1` to `5`
- scope feedback when the use case belongs to another team or manager
- feasibility feedback when the interviewee is in a position to judge it

The end-of-interview feasibility review is use-case-specific. The app separately judges whether the interviewee can comment on:

- data quality and availability
- regulatory or compliance risk
- explainability or auditability requirements

Those feasibility questions are asked one dimension at a time, only when they are relevant to that particular use case and that particular interviewee.

---

# Company Memory And Drafts

The app keeps two kinds of persistent memory:

Interview drafts

- used to restore an in-progress conversation after refresh or reconnect
- stored in persistent backend storage
- isolated per browser/client identity in the current setup

Company insights

- aggregated across completed interviews
- include previously identified tasks and AI use cases
- include employee validation feedback on proposed use cases
- now also include aggregated feasibility feedback such as:
  - average usefulness rating
  - average data-quality readiness score
  - average explainability score
  - regulatory-risk distribution
  - safe-to-pursue signals

---

# Unknown Tools And Terms

The app no longer assumes that the model already understands every tool or acronym the interviewee mentions.

If the interviewee introduces a named tool, system, acronym, or internal term that looks important to the workflow, the app can:

- ask the interviewee to explain what it is
- look for public context online when available
- ask the interviewee to confirm whether that public context is actually the same thing they mean
- store the clarified explanation and include it in later reasoning and report generation

This is especially useful for internal tools that are not safe to assume from model pretraining alone.

---

# Outputs

Each completed interview produces:

- a structured JSON report
- a Markdown report
- persisted company-level insight updates

Reports include:

- executive summary
- task inventory
- proposed AI use cases
- KPI suggestions
- feasibility assessment
- value-feasibility prioritization
- employee feedback on the proposed use cases

---

# Storage

Database backends

- MongoDB Atlas for shared drafts and server deployments
- SQLite for local-only fallback setups

Report storage backends

- S3-compatible object storage
- local filesystem fallback

Current configuration is controlled from `.env`.

---

# Project Structure

Important files and modules:

- [app/chainlit_app.py](/root/discovery/app/chainlit_app.py)
  Chainlit entrypoint and main runtime wiring
- [app/company_flow.py](/root/discovery/app/company_flow.py)
  metadata collection and company confirmation flow
- [app/question_flow.py](/root/discovery/app/question_flow.py)
  notes updates, readiness checks, and next-question planning
- [app/interview_flow.py](/root/discovery/app/interview_flow.py)
  company-confirmation handling, closeout flow, and use-case review logic
- [app/feedback_flow.py](/root/discovery/app/feedback_flow.py)
  report finalization and use-case feedback persistence
- [app/term_discovery.py](/root/discovery/app/term_discovery.py)
  clarification and public lookup for unknown tools and terms
- [app/company_research.py](/root/discovery/app/company_research.py)
  company website and public web research
- [app/db.py](/root/discovery/app/db.py)
  MongoDB / SQLite persistence
- [public/branding.js](/root/discovery/public/branding.js)
  favicon and browser branding
- [public/custom.css](/root/discovery/public/custom.css)
  UI styling and logo behavior
- [scripts/docker-run.sh](/root/discovery/scripts/docker-run.sh)
  build-and-run script used for Docker-based runs

---

# Environment Notes

The provided `.env.example` includes:

- OpenAI model settings
- optional SerpAPI support for better public lookup
- MongoDB Atlas settings
- S3-compatible report storage settings
- local SQLite and local report fallbacks

For server use, MongoDB Atlas and S3-compatible report storage are the intended default path.

---

# Contact

Konstantinos Tolanoudis  
ETH Zurich

ktolanoudis@ethz.ch
