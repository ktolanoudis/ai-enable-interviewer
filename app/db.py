import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv(override=False)

DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower()
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "data/sessions.db")
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ai_enable_discovery")


def _is_mongo_enabled() -> bool:
    return DB_BACKEND == "mongodb" and bool(MONGODB_URI)


def _mongo_collections():
    try:
        from pymongo import MongoClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "DB_BACKEND=mongodb requires pymongo. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB_NAME]
    return client, db["sessions"], db["company_insights"]


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _sqlite_ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    if column in _sqlite_columns(conn, table):
        return
    cur = conn.cursor()
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def init_db():
    if _is_mongo_enabled():
        client, sessions_col, insights_col = _mongo_collections()
        try:
            sessions_col.create_index([("company", 1), ("created_at", -1)])
            insights_col.create_index("company", unique=True)
        finally:
            client.close()
        return

    sqlite_dir = os.path.dirname(SQLITE_DB_PATH)
    if sqlite_dir:
        os.makedirs(sqlite_dir, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()

    c.execute(
        """CREATE TABLE IF NOT EXISTS sessions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  company TEXT,
                  employee TEXT,
                  department TEXT,
                  role TEXT,
                  seniority_level TEXT,
                  transcript TEXT,
                  report_json TEXT,
                  report_md TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )

    # Backward-compatible migration for existing SQLite files created by older schema versions.
    _sqlite_ensure_column(conn, "sessions", "department", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "role", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "seniority_level", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "report_json", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "created_at", "TIMESTAMP")
    c.execute("UPDATE sessions SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")

    c.execute(
        """CREATE TABLE IF NOT EXISTS company_insights
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  company TEXT UNIQUE,
                  north_star TEXT,
                  total_interviews INTEGER DEFAULT 0,
                  departments TEXT,
                  all_tasks TEXT,
                  all_use_cases TEXT,
                  validated_use_cases TEXT,
                  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )

    conn.commit()
    conn.close()


def save_session(
    company: str,
    employee: str,
    department: str,
    role: str,
    seniority_level: str,
    transcript: str,
    report_json: str,
    report_md: str,
):
    if _is_mongo_enabled():
        client, sessions_col, _ = _mongo_collections()
        try:
            sessions_col.insert_one(
                {
                    "company": company,
                    "employee": employee,
                    "department": department,
                    "role": role,
                    "seniority_level": seniority_level,
                    "transcript": transcript,
                    "report_json": report_json,
                    "report_md": report_md,
                    "created_at": datetime.utcnow(),
                }
            )
        finally:
            client.close()
        return

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute(
        """INSERT INTO sessions 
                 (company, employee, department, role, seniority_level, transcript, report_json, report_md) 
                 VALUES (?,?,?,?,?,?,?,?)""",
        (company, employee, department, role, seniority_level, transcript, report_json, report_md),
    )
    conn.commit()
    conn.close()


def get_company_interview_count(company: str) -> int:
    if _is_mongo_enabled():
        client, sessions_col, _ = _mongo_collections()
        try:
            return sessions_col.count_documents({"company": company})
        finally:
            client.close()

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sessions WHERE company = ?", (company,))
    count = c.fetchone()[0]
    conn.close()
    return count


def update_company_insights(
    company: str,
    north_star: Optional[str] = None,
    tasks: Optional[List] = None,
    use_cases: Optional[List] = None,
    validated_use_cases: Optional[List] = None,
):
    if _is_mongo_enabled():
        client, _, insights_col = _mongo_collections()
        try:
            existing = insights_col.find_one({"company": company}) or {}
            merged_tasks = list(existing.get("all_tasks", []))
            merged_use_cases = list(existing.get("all_use_cases", []))
            merged_validated = list(existing.get("validated_use_cases", []))

            if tasks:
                merged_tasks.extend(tasks)
            if use_cases:
                merged_use_cases.extend(use_cases)
            if validated_use_cases:
                merged_validated = list(validated_use_cases)

            new_doc = {
                "company": company,
                "north_star": north_star if north_star is not None else existing.get("north_star", ""),
                "total_interviews": int(existing.get("total_interviews", 0)) + 1,
                "departments": existing.get("departments", []),
                "all_tasks": merged_tasks,
                "all_use_cases": merged_use_cases,
                "validated_use_cases": merged_validated,
                "last_updated": datetime.utcnow().isoformat(),
            }
            insights_col.replace_one({"company": company}, new_doc, upsert=True)
        finally:
            client.close()
        return

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()

    c.execute("SELECT * FROM company_insights WHERE company = ?", (company,))
    existing = c.fetchone()

    if existing:
        updates = []
        params = []

        if north_star:
            updates.append("north_star = ?")
            params.append(north_star)

        if tasks:
            existing_tasks = json.loads(existing[5]) if existing[5] else []
            existing_tasks.extend(tasks)
            updates.append("all_tasks = ?")
            params.append(json.dumps(existing_tasks))

        if use_cases:
            existing_uc = json.loads(existing[6]) if existing[6] else []
            existing_uc.extend(use_cases)
            updates.append("all_use_cases = ?")
            params.append(json.dumps(existing_uc))

        if validated_use_cases:
            updates.append("validated_use_cases = ?")
            params.append(json.dumps(validated_use_cases))

        updates.append("total_interviews = total_interviews + 1")
        updates.append("last_updated = ?")
        params.append(datetime.now().isoformat())

        params.append(company)

        c.execute(f"UPDATE company_insights SET {', '.join(updates)} WHERE company = ?", params)
    else:
        c.execute(
            """INSERT INTO company_insights 
                     (company, north_star, total_interviews, all_tasks, all_use_cases, validated_use_cases)
                     VALUES (?,?,1,?,?,?)""",
            (
                company,
                north_star or "",
                json.dumps(tasks) if tasks else "[]",
                json.dumps(use_cases) if use_cases else "[]",
                json.dumps(validated_use_cases) if validated_use_cases else "[]",
            ),
        )

    conn.commit()
    conn.close()


def get_company_insights(company: str) -> Optional[Dict]:
    if _is_mongo_enabled():
        client, _, insights_col = _mongo_collections()
        try:
            doc = insights_col.find_one({"company": company})
            if not doc:
                return None
            return {
                "company": doc.get("company"),
                "north_star": doc.get("north_star", ""),
                "total_interviews": doc.get("total_interviews", 0),
                "departments": doc.get("departments", []),
                "all_tasks": doc.get("all_tasks", []),
                "all_use_cases": doc.get("all_use_cases", []),
                "validated_use_cases": doc.get("validated_use_cases", []),
                "last_updated": doc.get("last_updated"),
            }
        finally:
            client.close()

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM company_insights WHERE company = ?", (company,))
    row = c.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "company": row[1],
        "north_star": row[2],
        "total_interviews": row[3],
        "departments": json.loads(row[4]) if row[4] else [],
        "all_tasks": json.loads(row[5]) if row[5] else [],
        "all_use_cases": json.loads(row[6]) if row[6] else [],
        "validated_use_cases": json.loads(row[7]) if row[7] else [],
        "last_updated": row[8],
    }
