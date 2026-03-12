# AI-Enable: AI Use Case Discovery Agent

A multi‑stakeholder conversational AI system that discovers, analyzes, and prioritizes AI opportunities inside organizations.

The system conducts structured interviews with employees across different roles and departments, extracts operational tasks and bottlenecks, and generates a prioritized portfolio of AI use cases aligned with business goals.

---

# Quick Start

Requirements

- Python 3.13+
- OpenAI API key (or LiteLLM proxy)

Clone the repository

```bash
git clone https://github.com/ktolanoudis/ai-enable-interviewer.git
cd discovery
```

Create virtual environment

```bash
python -m venv venv
source venv/bin/activate
```

Install dependencies

```bash
pip install -r requirements.txt
```

Create environment file

```bash
cp .env.example .env
```

Add your API key:

```bash
OPENAI_API_KEY=sk-...
```

Run the application

```bash
chainlit run app/chainlit_app.py -w --port 8000
```

Open your browser:

```
http://localhost:8000
```

---

# Docker Deployment

Deployments use Docker Compose and rebuild from source on the server.

## One-Time Server Setup

```bash
git clone https://github.com/ktolanoudis/ai-enable-interviewer.git
cd ai-enable-interviewer
cp .env.example .env
# edit .env with required values
chmod +x scripts/server-setup.sh
./scripts/server-setup.sh
```

## Manual Deploy

From the repo directory on the server:

```bash
chmod +x scripts/deploy-prod.sh
./scripts/deploy-prod.sh
```

This pulls the latest `main`, rebuilds the image locally, and restarts the app via Compose.

## Automatic Deploy on Push

Configure these GitHub repository secrets and enable `.github/workflows/deploy-from-source.yml`:

- `DEPLOY_HOST` (server IP or hostname)
- `DEPLOY_PORT` (optional, default `22`)
- `DEPLOY_USER` (SSH user)
- `DEPLOY_PATH` (absolute path to cloned repo on server)
- `DEPLOY_SSH_KEY` (private key with access to that server)

The workflow SSHes into the server and runs:

```bash
BRANCH=main ./scripts/deploy-prod.sh
```

---

# Overview

Organizations are investing heavily in AI, yet most struggle to translate experimentation into real business value.

One major challenge is identifying **high-impact and feasible AI opportunities within existing workflows**.

This system solves that problem by combining:

- structured discovery interviews
- role-aware questioning
- cross-stakeholder knowledge aggregation
- automated AI opportunity analysis
- feasibility and value scoring

Instead of analyzing processes externally, the system collects **first-hand operational insights directly from employees** and converts them into structured AI opportunities.

---

# Key Features

## Multi‑Stakeholder Discovery

Interview multiple employees across the same organization and progressively build knowledge of workflows, bottlenecks, and automation opportunities.

## Role‑Aware Interviewing

Questions automatically adapt based on seniority:

Executive  
Strategic priorities and transformation goals.

Manager  
Department processes and operational bottlenecks.

Operational Staff  
Daily tasks and friction points.

## Organizational Memory

The system stores insights from previous interviews and uses them to:

- avoid repeating questions
- validate AI use cases
- accumulate organizational knowledge

## Automatic Company Context

The system automatically retrieves company information from the web and asks the user to confirm it before continuing.

---

# AI Opportunity Discovery Framework

The system implements a structured methodology consisting of four analytical stages.

STEP 2 — Task Identification  
Break down employee work into granular operational tasks.

STEP 3 — AI Use Case Discovery  
Match high-friction tasks with candidate AI solutions.

STEP 4 — KPI Definition  
Define measurable success metrics.

STEP 5 — Feasibility Evaluation  
Evaluate each use case based on data, regulatory constraints, and technical complexity.

---

# Outputs

Each interview generates:

JSON Report  
Machine-readable structured analysis.

Markdown Report  
Human-readable AI opportunity report.

Reports include:

- Executive summary
- Task inventory
- AI use case recommendations
- Value–feasibility prioritization
- Implementation recommendations

---

# Data Storage

Database

- SQLite (default)
- MongoDB (optional)

Report Storage

- Local filesystem
- S3-compatible object storage

---

# Example Use Cases

Academic Research  
Structured data collection for AI transformation studies.

Consulting  
Rapid discovery of AI opportunities within organizations.

Internal Innovation  
Identify automation opportunities across departments.

Investment Due Diligence  
Assess AI potential in portfolio companies.

---

# Contact

Konstantinos Tolanoudis  
ETH Zurich

ktolanoudis@ethz.ch
