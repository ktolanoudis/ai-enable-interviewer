import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from dotenv import load_dotenv
from company_memory import resolve_recurring_theme

load_dotenv(override=False)

DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower()
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH", "data/sessions.db")
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "ai_enable_discovery")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SQLITE_BUSY_TIMEOUT_MS = _int_env("SQLITE_BUSY_TIMEOUT_MS", 30000)
MONGO_COMPANY_LOCK_TIMEOUT_SECONDS = _float_env("MONGO_COMPANY_LOCK_TIMEOUT_SECONDS", 10.0)
MONGO_COMPANY_LOCK_STALE_SECONDS = _int_env("MONGO_COMPANY_LOCK_STALE_SECONDS", 60)


def _is_mongo_enabled() -> bool:
    return DB_BACKEND == "mongodb" and bool(MONGODB_URI)


def _mongo_collections():
    try:
        from pymongo import MongoClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "DB_BACKEND=mongodb requires pymongo. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    client_kwargs = {}
    try:
        import certifi

        client_kwargs["tlsCAFile"] = certifi.where()
    except Exception:
        pass

    client = MongoClient(MONGODB_URI, **client_kwargs)
    db = client[MONGODB_DB_NAME]
    return client, db["sessions"], db["company_insights"]


def _mongo_drafts_collection():
    try:
        from pymongo import MongoClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "DB_BACKEND=mongodb requires pymongo. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    client_kwargs = {}
    try:
        import certifi

        client_kwargs["tlsCAFile"] = certifi.where()
    except Exception:
        pass

    client = MongoClient(MONGODB_URI, **client_kwargs)
    db = client[MONGODB_DB_NAME]
    return client, db["interview_drafts"]


def _sqlite_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_DB_PATH, timeout=max(1.0, SQLITE_BUSY_TIMEOUT_MS / 1000.0))
    conn.execute(f"PRAGMA busy_timeout = {max(1, SQLITE_BUSY_TIMEOUT_MS)}")
    return conn


def _mongo_company_locks_collection(client):
    return client[MONGODB_DB_NAME]["company_insight_locks"]


def _acquire_mongo_company_lock(locks_col, company_key: str) -> str:
    try:
        from pymongo.errors import DuplicateKeyError
    except ModuleNotFoundError:
        DuplicateKeyError = Exception

    lock_key = f"company_insights:{company_key or 'unknown'}"
    token = uuid.uuid4().hex
    deadline = time.monotonic() + MONGO_COMPANY_LOCK_TIMEOUT_SECONDS
    while True:
        now = datetime.utcnow()
        try:
            locks_col.delete_many({"lock_key": lock_key, "expires_at": {"$lte": now}})
            locks_col.insert_one(
                {
                    "lock_key": lock_key,
                    "token": token,
                    "created_at": now,
                    "expires_at": now + timedelta(seconds=MONGO_COMPANY_LOCK_STALE_SECONDS),
                }
            )
            return token
        except DuplicateKeyError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out acquiring company memory lock for {company_key}")
            time.sleep(0.05)


def _release_mongo_company_lock(locks_col, company_key: str, token: str) -> None:
    lock_key = f"company_insights:{company_key or 'unknown'}"
    locks_col.delete_one({"lock_key": lock_key, "token": token})


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _sqlite_ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
    if column in _sqlite_columns(conn, table):
        return
    cur = conn.cursor()
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def _normalize_use_case_feedback_key(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _normalize_text(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _is_blankish(value: Optional[str]) -> bool:
    return _normalize_text(value) in {"", "not specified", "unknown", "n/a", "na", "none", "null"}


def _clean_text(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().split())


def _normalize_company_key(value: Optional[str]) -> str:
    return _normalize_text(value)


def _merge_company_display_name(existing: Optional[str], new: Optional[str]) -> str:
    existing_text = _clean_text(existing)
    new_text = _clean_text(new)
    if existing_text and new_text:
        if _normalize_company_key(existing_text) == _normalize_company_key(new_text):
            if existing_text == existing_text.lower() and new_text != new_text.lower():
                return new_text
    return existing_text or new_text


def _mongo_company_match_query(company: Optional[str]) -> Dict:
    company_key = _normalize_company_key(company)
    exact_company = _clean_text(company)
    clauses = []
    if company_key:
        clauses.append({"company_key": company_key})
    if exact_company:
        clauses.append({"company": {"$regex": f"^{re.escape(exact_company)}$", "$options": "i"}})
    if not clauses:
        return {"company": exact_company}
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}


def _merge_unique_strings(existing_values: Optional[List], new_values: Optional[List]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for raw in list(existing_values or []) + list(new_values or []):
        text = _clean_text(raw)
        norm = _normalize_text(text)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        merged.append(text)
    return sorted(merged, key=lambda item: item.lower())


def _coerce_string_list(values) -> List[str]:
    return _merge_unique_strings([], values if isinstance(values, list) else [])


def _coerce_contributor_keys(values) -> List[str]:
    out = []
    seen = set()
    for raw in values if isinstance(values, list) else []:
        text = _clean_text(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _tokenize_for_key(*values: Optional[str]) -> set[str]:
    tokens = set()
    for value in values:
        cleaned = str(value or "").lower().replace("_", " ")
        tokens.update(re.findall(r"[a-z0-9]+", cleaned))
    return tokens


def _slugify_theme_key(value: Optional[str]) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", _normalize_text(value))
    return text.strip("_")[:80]


def _canonicalize_task_key(item: Dict) -> str:
    name = _normalize_text(item.get("name"))
    department = _normalize_text(item.get("department"))
    if not name:
        return ""
    return f"{department}::{name}" if department else name


def _canonicalize_use_case_key(item: Dict) -> str:
    task_name = _normalize_text(item.get("task_name"))
    ai_solution_type = _normalize_text(item.get("ai_solution_type"))
    use_case_name = _normalize_text(item.get("use_case_name"))
    if task_name and ai_solution_type:
        return f"{task_name}::{ai_solution_type}"
    if task_name:
        return task_name
    return use_case_name


def _canonicalize_validated_use_case_feedback_key(item: Dict) -> str:
    task_name = _normalize_text(item.get("task_name"))
    ai_solution_type = _normalize_text(item.get("ai_solution_type"))
    use_case_name = _normalize_text(item.get("use_case_name"))
    if task_name and ai_solution_type:
        return f"{task_name}::{ai_solution_type}"
    if task_name:
        return task_name
    return use_case_name


def _canonicalize_theme_identity(item: Dict) -> tuple[str, str, str]:
    raw_key = str(item.get("theme_key", "")).strip().lower().replace(" ", "_")
    label = _clean_text(item.get("label"))
    category = _normalize_text(item.get("category")) or "workflow"
    evidence = _clean_text(item.get("evidence")) or _clean_text(item.get("contradiction_evidence"))

    if not raw_key:
        raw_key = _slugify_theme_key(label or evidence)
    if not label:
        label = raw_key.replace("_", " ").strip()
    return raw_key, label, category


def _coerce_task_entry(item: Dict) -> Optional[Dict]:
    if not isinstance(item, dict):
        return None
    name = _clean_text(item.get("name"))
    if not name:
        return None
    entry = dict(item)
    entry["name"] = name
    entry["description"] = _clean_text(item.get("description"))
    entry["department"] = _clean_text(item.get("department"))
    entry["frequency"] = _clean_text(item.get("frequency"))
    entry["time_spent"] = _clean_text(item.get("time_spent"))
    entry["friction_level"] = _normalize_text(item.get("friction_level"))
    entry["friction_points"] = _coerce_string_list(item.get("friction_points"))
    entry["current_systems"] = _coerce_string_list(item.get("current_systems"))
    entry["manual_steps"] = _coerce_string_list(item.get("manual_steps"))
    entry["departments"] = _merge_unique_strings(item.get("departments"), [entry["department"]])
    entry["contributor_keys"] = _coerce_contributor_keys(item.get("contributor_keys"))
    existing_count = int(item.get("mention_count", 0) or 0)
    entry["mention_count"] = existing_count if existing_count > 0 else max(1, len(entry["contributor_keys"]) or 1)
    return entry


def _merge_aggregated_tasks(existing_items: Optional[List], new_items: Optional[List], contributor_key: Optional[str] = None) -> List[Dict]:
    merged: Dict[str, Dict] = {}

    for item in existing_items or []:
        coerced = _coerce_task_entry(item)
        if not coerced:
            continue
        key = _canonicalize_task_key(coerced)
        if key:
            merged[key] = coerced

    for item in new_items or []:
        coerced = _coerce_task_entry(item)
        if not coerced:
            continue
        key = _canonicalize_task_key(coerced)
        if not key:
            continue
        current = merged.get(key)
        if not current:
            if contributor_key:
                coerced["contributor_keys"] = _coerce_contributor_keys(coerced.get("contributor_keys")) + [contributor_key]
                coerced["contributor_keys"] = _coerce_contributor_keys(coerced["contributor_keys"])
            coerced["mention_count"] = 1 if contributor_key else max(1, int(coerced.get("mention_count", 1) or 1))
            merged[key] = coerced
            continue

        if len(coerced.get("description", "")) > len(current.get("description", "")):
            current["description"] = coerced["description"]
        if _is_blankish(current.get("frequency")) and not _is_blankish(coerced.get("frequency")):
            current["frequency"] = coerced["frequency"]
        if _is_blankish(current.get("time_spent")) and not _is_blankish(coerced.get("time_spent")):
            current["time_spent"] = coerced["time_spent"]
        if _is_blankish(current.get("friction_level")) and not _is_blankish(coerced.get("friction_level")):
            current["friction_level"] = coerced["friction_level"]

        current["friction_points"] = _merge_unique_strings(current.get("friction_points"), coerced.get("friction_points"))
        current["current_systems"] = _merge_unique_strings(current.get("current_systems"), coerced.get("current_systems"))
        current["manual_steps"] = _merge_unique_strings(current.get("manual_steps"), coerced.get("manual_steps"))
        current["departments"] = _merge_unique_strings(current.get("departments"), coerced.get("departments"))
        if not current.get("department") and coerced.get("department"):
            current["department"] = coerced["department"]

        if contributor_key:
            contributor_keys = _coerce_contributor_keys(current.get("contributor_keys"))
            if contributor_key not in contributor_keys:
                contributor_keys.append(contributor_key)
                current["contributor_keys"] = contributor_keys
                current["mention_count"] = int(current.get("mention_count", 0) or 0) + 1
        else:
            current["mention_count"] = int(current.get("mention_count", 0) or 0) + 1

    return sorted(
        merged.values(),
        key=lambda item: (-int(item.get("mention_count", 0) or 0), str(item.get("name", ""))),
    )


def _coerce_use_case_entry(item: Dict) -> Optional[Dict]:
    if not isinstance(item, dict):
        return None
    use_case_name = _clean_text(item.get("use_case_name"))
    if not use_case_name:
        return None
    entry = dict(item)
    entry["use_case_name"] = use_case_name
    entry["task_name"] = _clean_text(item.get("task_name"))
    entry["description"] = _clean_text(item.get("description"))
    entry["business_alignment"] = _clean_text(item.get("business_alignment"))
    entry["expected_impact"] = _clean_text(item.get("expected_impact"))
    entry["data_quality"] = _normalize_text(item.get("data_quality"))
    entry["data_requirements"] = _clean_text(item.get("data_requirements"))
    entry["regulatory_risk"] = _normalize_text(item.get("regulatory_risk"))
    entry["technical_feasibility"] = _normalize_text(item.get("technical_feasibility"))
    entry["implementation_effort"] = _clean_text(item.get("implementation_effort"))
    entry["priority_quadrant"] = _clean_text(item.get("priority_quadrant"))
    entry["ai_solution_type"] = _clean_text(item.get("ai_solution_type"))
    entry["kpis"] = _coerce_string_list(item.get("kpis"))
    entry["regulatory_concerns"] = _coerce_string_list(item.get("regulatory_concerns"))
    entry["contributor_keys"] = _coerce_contributor_keys(item.get("contributor_keys"))
    entry["source_use_case_names"] = _merge_unique_strings(item.get("source_use_case_names"), [use_case_name])
    existing_count = int(item.get("mention_count", 0) or 0)
    entry["mention_count"] = existing_count if existing_count > 0 else max(1, len(entry["contributor_keys"]) or 1)
    return entry


def _merge_aggregated_use_cases(existing_items: Optional[List], new_items: Optional[List], contributor_key: Optional[str] = None) -> List[Dict]:
    merged: Dict[str, Dict] = {}

    for item in existing_items or []:
        coerced = _coerce_use_case_entry(item)
        if not coerced:
            continue
        key = _canonicalize_use_case_key(coerced)
        if key:
            merged[key] = coerced

    for item in new_items or []:
        coerced = _coerce_use_case_entry(item)
        if not coerced:
            continue
        key = _canonicalize_use_case_key(coerced)
        if not key:
            continue
        current = merged.get(key)
        if not current:
            if contributor_key:
                coerced["contributor_keys"] = _coerce_contributor_keys(coerced.get("contributor_keys")) + [contributor_key]
                coerced["contributor_keys"] = _coerce_contributor_keys(coerced["contributor_keys"])
            coerced["mention_count"] = 1 if contributor_key else max(1, int(coerced.get("mention_count", 1) or 1))
            merged[key] = coerced
            continue

        if len(coerced.get("description", "")) > len(current.get("description", "")):
            current["description"] = coerced["description"]
        if coerced.get("task_name") and not current.get("task_name"):
            current["task_name"] = coerced["task_name"]
        if len(coerced.get("business_alignment", "")) > len(current.get("business_alignment", "")):
            current["business_alignment"] = coerced["business_alignment"]
        if _is_blankish(current.get("expected_impact")) and not _is_blankish(coerced.get("expected_impact")):
            current["expected_impact"] = coerced["expected_impact"]
        if _is_blankish(current.get("data_quality")) and not _is_blankish(coerced.get("data_quality")):
            current["data_quality"] = coerced["data_quality"]
        if len(coerced.get("data_requirements", "")) > len(current.get("data_requirements", "")):
            current["data_requirements"] = coerced["data_requirements"]
        if _is_blankish(current.get("regulatory_risk")) and not _is_blankish(coerced.get("regulatory_risk")):
            current["regulatory_risk"] = coerced["regulatory_risk"]
        if _is_blankish(current.get("technical_feasibility")) and not _is_blankish(coerced.get("technical_feasibility")):
            current["technical_feasibility"] = coerced["technical_feasibility"]
        if _is_blankish(current.get("implementation_effort")) and not _is_blankish(coerced.get("implementation_effort")):
            current["implementation_effort"] = coerced["implementation_effort"]
        if _is_blankish(current.get("priority_quadrant")) and not _is_blankish(coerced.get("priority_quadrant")):
            current["priority_quadrant"] = coerced["priority_quadrant"]
        if not current.get("ai_solution_type") and coerced.get("ai_solution_type"):
            current["ai_solution_type"] = coerced["ai_solution_type"]

        for numeric_field in ("value_score", "feasibility_score"):
            if current.get(numeric_field) in {None, ""} and coerced.get(numeric_field) not in {None, ""}:
                current[numeric_field] = coerced.get(numeric_field)

        current["kpis"] = _merge_unique_strings(current.get("kpis"), coerced.get("kpis"))
        current["regulatory_concerns"] = _merge_unique_strings(current.get("regulatory_concerns"), coerced.get("regulatory_concerns"))
        current["source_use_case_names"] = _merge_unique_strings(current.get("source_use_case_names"), coerced.get("source_use_case_names"))

        if contributor_key:
            contributor_keys = _coerce_contributor_keys(current.get("contributor_keys"))
            if contributor_key not in contributor_keys:
                contributor_keys.append(contributor_key)
                current["contributor_keys"] = contributor_keys
                current["mention_count"] = int(current.get("mention_count", 0) or 0) + 1
        else:
            current["mention_count"] = int(current.get("mention_count", 0) or 0) + 1

    return sorted(
        merged.values(),
        key=lambda item: (-int(item.get("mention_count", 0) or 0), str(item.get("use_case_name", ""))),
    )


def _merge_north_star(existing: Optional[str], new_value: Optional[str]) -> str:
    existing_text = str(existing or "").strip()
    new_text = str(new_value or "").strip()

    if not existing_text:
        return new_text
    if not new_text:
        return existing_text

    existing_norm = " ".join(existing_text.lower().split())
    new_norm = " ".join(new_text.lower().split())
    if existing_norm == new_norm:
        return existing_text
    if new_norm in existing_norm:
        return existing_text
    if existing_norm in new_norm:
        return new_text

    return (
        "North Star perspectives collected from senior stakeholders:\n"
        f"- {existing_text}\n"
        f"- {new_text}"
    )


def _merge_recurring_themes(existing_items: Optional[List], new_items: Optional[List], contributor_key: Optional[str] = None) -> List[Dict]:
    merged: Dict[str, Dict] = {}

    def _apply_existing(item: Dict) -> None:
        if not isinstance(item, dict):
            return
        theme_key, label, category = _canonicalize_theme_identity(item)
        if not theme_key or not label:
            return
        examples = []
        evidence = _clean_text(item.get("evidence"))
        if evidence:
            examples.append(evidence)
        for extra in item.get("examples", []) if isinstance(item.get("examples"), list) else []:
            extra_text = str(extra or "").strip()
            if extra_text and extra_text not in examples:
                examples.append(extra_text)
        contradiction_examples = []
        contradiction_evidence = _clean_text(item.get("contradiction_evidence"))
        if contradiction_evidence:
            contradiction_examples.append(contradiction_evidence)
        for extra in item.get("contradiction_examples", []) if isinstance(item.get("contradiction_examples"), list) else []:
            extra_text = str(extra or "").strip()
            if extra_text and extra_text not in contradiction_examples:
                contradiction_examples.append(extra_text)
        supporting_contributors = _coerce_contributor_keys(
            item.get("supporting_contributor_keys") or item.get("contributor_keys")
        )
        contradicting_contributors = _coerce_contributor_keys(item.get("contradicting_contributor_keys"))
        mention_count = int(item.get("mention_count", item.get("count", 0)) or 0)
        contradiction_count = int(item.get("contradiction_count", 0) or 0)
        if mention_count <= 0:
            mention_count = max(len(supporting_contributors), 1 if examples and contradiction_count <= 0 else 0)
        if contradiction_count <= 0:
            contradiction_count = len(contradicting_contributors)
        merged[theme_key] = {
            "theme_key": theme_key,
            "label": label,
            "category": category,
            "mention_count": mention_count,
            "contradiction_count": contradiction_count,
            "examples": examples[:3],
            "contradiction_examples": contradiction_examples[:3],
            "supporting_contributor_keys": supporting_contributors,
            "contradicting_contributor_keys": contradicting_contributors,
            "last_updated": item.get("last_updated"),
        }

    def _apply_new(item: Dict) -> None:
        if not isinstance(item, dict):
            return
        theme_key, label, category = _canonicalize_theme_identity(item)
        if not theme_key or not label:
            return
        entry = merged.setdefault(
            theme_key,
            {
                "theme_key": theme_key,
                "label": label,
                "category": category,
                "mention_count": 0,
                "contradiction_count": 0,
                "examples": [],
                "contradiction_examples": [],
                "supporting_contributor_keys": [],
                "contradicting_contributor_keys": [],
                "last_updated": None,
            },
        )
        support_increment = item.get("mention_count")
        if support_increment is None:
            support_increment = item.get("count")
        if support_increment is None:
            support_increment = 0 if item.get("contradiction_count") else 1
        support_increment = int(support_increment or 0)
        contradiction_increment = int(item.get("contradiction_count", 0) or 0)
        support_evidence = _clean_text(item.get("evidence"))
        contradiction_evidence = _clean_text(item.get("contradiction_evidence"))

        if support_increment > 0:
            if contributor_key:
                contributor_keys = _coerce_contributor_keys(entry.get("supporting_contributor_keys"))
                if contributor_key not in contributor_keys:
                    contributor_keys.append(contributor_key)
                    entry["supporting_contributor_keys"] = contributor_keys
                    entry["mention_count"] = int(entry.get("mention_count", 0) or 0) + support_increment
                    if support_evidence and support_evidence not in entry["examples"]:
                        entry["examples"].append(support_evidence)
            else:
                entry["mention_count"] = int(entry.get("mention_count", 0) or 0) + support_increment
                if support_evidence and support_evidence not in entry["examples"]:
                    entry["examples"].append(support_evidence)

        if contradiction_increment > 0:
            if contributor_key:
                contributor_keys = _coerce_contributor_keys(entry.get("contradicting_contributor_keys"))
                if contributor_key not in contributor_keys:
                    contributor_keys.append(contributor_key)
                    entry["contradicting_contributor_keys"] = contributor_keys
                    entry["contradiction_count"] = int(entry.get("contradiction_count", 0) or 0) + contradiction_increment
                    if contradiction_evidence and contradiction_evidence not in entry["contradiction_examples"]:
                        entry["contradiction_examples"].append(contradiction_evidence)
            else:
                entry["contradiction_count"] = int(entry.get("contradiction_count", 0) or 0) + contradiction_increment
                if contradiction_evidence and contradiction_evidence not in entry["contradiction_examples"]:
                    entry["contradiction_examples"].append(contradiction_evidence)

        entry["examples"] = entry["examples"][:3]
        entry["contradiction_examples"] = entry["contradiction_examples"][:3]
        entry["last_updated"] = item.get("last_updated") or entry.get("last_updated")

    for item in existing_items or []:
        _apply_existing(item)
    for item in new_items or []:
        resolved = resolve_recurring_theme(list(merged.values()), item)
        _apply_new(resolved or item)

    return sorted(
        merged.values(),
        key=lambda x: (
            -(int(x.get("mention_count", 0) or 0) - int(x.get("contradiction_count", 0) or 0)),
            -int(x.get("mention_count", 0) or 0),
            str(x.get("label", "")),
        ),
    )


def _merge_validated_use_case_feedback(existing_items: Optional[List], new_items: Optional[List]) -> List[Dict]:
    merged: Dict[str, Dict] = {}

    def _coerce_entry(item: Dict) -> Optional[Dict]:
        if not isinstance(item, dict):
            return None
        use_case_name = _clean_text(item.get("use_case_name"))
        if not use_case_name:
            return None

        comments = item.get("comments")
        if not isinstance(comments, list):
            comments = []
        normalized_comments = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            normalized_comment = dict(comment)
            normalized_comment["employee"] = _clean_text(comment.get("employee"))
            normalized_comment["role"] = _clean_text(comment.get("role"))
            normalized_comment["department"] = _clean_text(comment.get("department"))
            normalized_comment["comment"] = _clean_text(comment.get("comment"))
            normalized_comment["contributor_key"] = _clean_text(comment.get("contributor_key"))
            normalized_comments.append(normalized_comment)

        rating_count = int(item.get("rating_count", 0) or 0)
        rating_sum = float(item.get("rating_sum", 0) or 0)
        average_rating = round(rating_sum / rating_count, 2) if rating_count > 0 else None
        contributor_keys = _coerce_contributor_keys(item.get("contributor_keys"))
        for comment in normalized_comments:
            contributor_key = _clean_text(comment.get("contributor_key"))
            if contributor_key and contributor_key not in contributor_keys:
                contributor_keys.append(contributor_key)

        return {
            "use_case_name": use_case_name,
            "task_name": _clean_text(item.get("task_name")),
            "ai_solution_type": _clean_text(item.get("ai_solution_type")),
            "latest_description": _clean_text(item.get("latest_description")),
            "rating_count": rating_count,
            "rating_sum": rating_sum,
            "average_rating": average_rating,
            "support_count": int(item.get("support_count", 0) or 0),
            "concern_count": int(item.get("concern_count", 0) or 0),
            "data_quality_score_count": int(item.get("data_quality_score_count", 0) or 0),
            "data_quality_score_sum": float(item.get("data_quality_score_sum", 0) or 0),
            "average_data_quality_score": item.get("average_data_quality_score"),
            "explainability_score_count": int(item.get("explainability_score_count", 0) or 0),
            "explainability_score_sum": float(item.get("explainability_score_sum", 0) or 0),
            "average_explainability_score": item.get("average_explainability_score"),
            "regulatory_risk_counts": {
                "low": int(((item.get("regulatory_risk_counts") or {}).get("low", 0)) or 0),
                "medium": int(((item.get("regulatory_risk_counts") or {}).get("medium", 0)) or 0),
                "high": int(((item.get("regulatory_risk_counts") or {}).get("high", 0)) or 0),
                "critical": int(((item.get("regulatory_risk_counts") or {}).get("critical", 0)) or 0),
                "unknown": int(((item.get("regulatory_risk_counts") or {}).get("unknown", 0)) or 0),
            },
            "safe_to_pursue_counts": {
                "yes": int(((item.get("safe_to_pursue_counts") or {}).get("yes", 0)) or 0),
                "no": int(((item.get("safe_to_pursue_counts") or {}).get("no", 0)) or 0),
                "unclear": int(((item.get("safe_to_pursue_counts") or {}).get("unclear", 0)) or 0),
            },
            "contributor_keys": contributor_keys,
            "comments": normalized_comments,
            "last_updated": item.get("last_updated"),
        }

    def _apply(item: Dict) -> None:
        entry = _coerce_entry(item)
        if not entry:
            return

        key = _canonicalize_validated_use_case_feedback_key(entry)
        if key not in merged:
            merged[key] = entry
            return

        current = merged[key]
        existing_contributors = _coerce_contributor_keys(current.get("contributor_keys"))
        incoming_contributors = _coerce_contributor_keys(entry.get("contributor_keys"))
        is_duplicate_contributor = bool(incoming_contributors) and all(
            contributor in existing_contributors for contributor in incoming_contributors
        )

        if not is_duplicate_contributor:
            current["rating_count"] += entry["rating_count"]
            current["rating_sum"] += entry["rating_sum"]
            current["average_rating"] = (
                round(current["rating_sum"] / current["rating_count"], 2)
                if current["rating_count"] > 0
                else None
            )
            current["support_count"] += entry["support_count"]
            current["concern_count"] += entry["concern_count"]
            current["data_quality_score_count"] += entry["data_quality_score_count"]
            current["data_quality_score_sum"] += entry["data_quality_score_sum"]
            current["average_data_quality_score"] = (
                round(current["data_quality_score_sum"] / current["data_quality_score_count"], 2)
                if current["data_quality_score_count"] > 0
                else None
            )
            current["explainability_score_count"] += entry["explainability_score_count"]
            current["explainability_score_sum"] += entry["explainability_score_sum"]
            current["average_explainability_score"] = (
                round(current["explainability_score_sum"] / current["explainability_score_count"], 2)
                if current["explainability_score_count"] > 0
                else None
            )
            for risk_key in current["regulatory_risk_counts"]:
                current["regulatory_risk_counts"][risk_key] += entry["regulatory_risk_counts"].get(risk_key, 0)
            for pursue_key in current["safe_to_pursue_counts"]:
                current["safe_to_pursue_counts"][pursue_key] += entry["safe_to_pursue_counts"].get(pursue_key, 0)
            if isinstance(entry.get("comments"), list):
                current["comments"].extend(entry["comments"])
            current["contributor_keys"] = _coerce_contributor_keys(existing_contributors + incoming_contributors)

        if entry.get("task_name") and not current.get("task_name"):
            current["task_name"] = entry["task_name"]
        if entry.get("ai_solution_type") and not current.get("ai_solution_type"):
            current["ai_solution_type"] = entry["ai_solution_type"]
        if entry.get("latest_description"):
            current["latest_description"] = entry["latest_description"]
        current["last_updated"] = entry.get("last_updated") or current.get("last_updated")

    for item in existing_items or []:
        _apply(item)
    for item in new_items or []:
        _apply(item)

    return list(merged.values())


def init_db():
    if _is_mongo_enabled():
        client, sessions_col, insights_col = _mongo_collections()
        drafts_client, drafts_col = _mongo_drafts_collection()
        try:
            locks_col = _mongo_company_locks_collection(client)
            sessions_col.create_index([("company", 1), ("created_at", -1)])
            sessions_col.create_index([("company_key", 1), ("created_at", -1)])
            sessions_col.create_index("contributor_key")
            insights_col.create_index("company", unique=True)
            insights_col.create_index("company_key")
            drafts_col.create_index("draft_id", unique=True)
            drafts_col.create_index([("updated_at", -1)])
            locks_col.create_index("lock_key", unique=True)
            locks_col.create_index("expires_at", expireAfterSeconds=0)
        finally:
            client.close()
            drafts_client.close()
        return

    sqlite_dir = os.path.dirname(SQLITE_DB_PATH)
    if sqlite_dir:
        os.makedirs(sqlite_dir, exist_ok=True)
    conn = _sqlite_connect()
    c = conn.cursor()

    c.execute(
        """CREATE TABLE IF NOT EXISTS sessions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  company TEXT,
                  company_key TEXT,
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
    _sqlite_ensure_column(conn, "sessions", "company_key", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "department", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "role", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "seniority_level", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "report_json", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "contributor_key", "TEXT")
    _sqlite_ensure_column(conn, "sessions", "created_at", "TIMESTAMP")
    c.execute("UPDATE sessions SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
    c.execute("UPDATE sessions SET company_key = lower(trim(company)) WHERE (company_key IS NULL OR company_key = '') AND company IS NOT NULL")

    c.execute(
        """CREATE TABLE IF NOT EXISTS company_insights
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  company TEXT UNIQUE,
                  company_key TEXT,
                  north_star TEXT,
                  total_interviews INTEGER DEFAULT 0,
                  departments TEXT,
                  all_tasks TEXT,
                  all_use_cases TEXT,
                  validated_use_cases TEXT,
                  recurring_themes TEXT,
                  last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    _sqlite_ensure_column(conn, "company_insights", "company_key", "TEXT")
    _sqlite_ensure_column(conn, "company_insights", "recurring_themes", "TEXT")
    _sqlite_ensure_column(conn, "company_insights", "contributor_keys", "TEXT")
    _sqlite_ensure_column(conn, "company_insights", "total_sessions", "INTEGER DEFAULT 0")
    c.execute("UPDATE company_insights SET company_key = lower(trim(company)) WHERE (company_key IS NULL OR company_key = '') AND company IS NOT NULL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_company_key ON sessions(company_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_contributor_key ON sessions(contributor_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_company_insights_company_key ON company_insights(company_key)")

    c.execute(
        """CREATE TABLE IF NOT EXISTS interview_drafts
                 (draft_id TEXT PRIMARY KEY,
                  payload TEXT,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
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
    contributor: Optional[Dict] = None,
):
    company_display = _clean_text(company)
    company_key = _normalize_company_key(company_display)
    contributor = contributor or {}
    contributor_key = _clean_text(contributor.get("contributor_key"))
    if _is_mongo_enabled():
        client, sessions_col, _ = _mongo_collections()
        try:
            sessions_col.insert_one(
                {
                    "company": company_display,
                    "company_key": company_key,
                    "employee": employee,
                    "department": department,
                    "role": role,
                    "seniority_level": seniority_level,
                    "transcript": transcript,
                    "report_json": report_json,
                    "report_md": report_md,
                    "contributor_key": contributor_key,
                    "contributor": {
                        "contributor_key": contributor_key,
                        "employee": _clean_text(contributor.get("employee")),
                        "role": _clean_text(contributor.get("role")),
                        "department": _clean_text(contributor.get("department")),
                    },
                    "created_at": datetime.utcnow(),
                }
            )
        finally:
            client.close()
        return

    conn = _sqlite_connect()
    c = conn.cursor()
    c.execute(
        """INSERT INTO sessions 
                 (company, company_key, employee, department, role, seniority_level, transcript, report_json, report_md, contributor_key) 
                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            company_display,
            company_key,
            employee,
            department,
            role,
            seniority_level,
            transcript,
            report_json,
            report_md,
            contributor_key,
        ),
    )
    conn.commit()
    conn.close()


def get_company_interview_count(company: str) -> int:
    company_key = _normalize_company_key(company)
    if _is_mongo_enabled():
        client, sessions_col, _ = _mongo_collections()
        try:
            return sessions_col.count_documents(_mongo_company_match_query(company))
        finally:
            client.close()

    conn = _sqlite_connect()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM sessions WHERE company_key = ? OR lower(trim(company)) = ?",
        (company_key, company_key),
    )
    count = c.fetchone()[0]
    conn.close()
    return count


def update_company_insights(
    company: str,
    north_star: Optional[str] = None,
    department: Optional[str] = None,
    tasks: Optional[List] = None,
    use_cases: Optional[List] = None,
    validated_use_cases: Optional[List] = None,
    recurring_themes: Optional[List] = None,
    contributor: Optional[Dict] = None,
):
    company_display = _clean_text(company)
    company_key = _normalize_company_key(company_display)
    contributor_key = _clean_text((contributor or {}).get("contributor_key"))
    merged_department = _clean_text(department)
    if _is_mongo_enabled():
        client, _, insights_col = _mongo_collections()
        locks_col = _mongo_company_locks_collection(client)
        lock_token = ""
        try:
            lock_token = _acquire_mongo_company_lock(locks_col, company_key)
            existing = insights_col.find_one(_mongo_company_match_query(company_display)) or {}
            existing_contributor_keys = _coerce_contributor_keys(existing.get("contributor_keys", []))
            merged_contributor_keys = _coerce_contributor_keys(existing_contributor_keys + ([contributor_key] if contributor_key else []))
            total_sessions = int(existing.get("total_sessions", existing.get("total_interviews", 0)) or 0) + 1
            distinct_contributor_count = len(merged_contributor_keys) if merged_contributor_keys else total_sessions
            merged_departments = _merge_unique_strings(existing.get("departments", []), [merged_department])
            merged_tasks = list(existing.get("all_tasks", []))
            merged_use_cases = list(existing.get("all_use_cases", []))
            merged_validated = list(existing.get("validated_use_cases", []))
            merged_themes = list(existing.get("recurring_themes", []))

            if tasks:
                merged_tasks = _merge_aggregated_tasks(merged_tasks, tasks, contributor_key=contributor_key)
            if use_cases:
                merged_use_cases = _merge_aggregated_use_cases(merged_use_cases, use_cases, contributor_key=contributor_key)
            if validated_use_cases:
                merged_validated = _merge_validated_use_case_feedback(merged_validated, validated_use_cases)
            if recurring_themes:
                merged_themes = _merge_recurring_themes(merged_themes, recurring_themes, contributor_key=contributor_key)

            new_doc = {
                "company": _merge_company_display_name(existing.get("company"), company_display),
                "company_key": company_key,
                "north_star": _merge_north_star(existing.get("north_star", ""), north_star),
                "total_interviews": distinct_contributor_count,
                "total_sessions": total_sessions,
                "contributor_keys": merged_contributor_keys,
                "departments": merged_departments,
                "all_tasks": merged_tasks,
                "all_use_cases": merged_use_cases,
                "validated_use_cases": merged_validated,
                "recurring_themes": merged_themes,
                "last_updated": datetime.utcnow().isoformat(),
            }
            if existing.get("_id") is not None:
                insights_col.replace_one({"_id": existing["_id"]}, new_doc, upsert=True)
            else:
                insights_col.replace_one({"company_key": company_key}, new_doc, upsert=True)
        finally:
            if lock_token:
                try:
                    _release_mongo_company_lock(locks_col, company_key, lock_token)
                except Exception:
                    pass
            client.close()
        return

    conn = _sqlite_connect()
    try:
        conn.isolation_level = None
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")

        c.execute(
            """SELECT id, company, company_key, north_star, total_interviews, departments, all_tasks, all_use_cases,
                      validated_use_cases, recurring_themes, last_updated, contributor_keys, total_sessions
               FROM company_insights
               WHERE company_key = ? OR lower(trim(company)) = ?
               ORDER BY CASE WHEN company_key = ? THEN 0 ELSE 1 END, id ASC
               LIMIT 1""",
            (company_key, company_key, company_key),
        )
        existing = c.fetchone()

        if existing:
            updates = []
            params = []
            existing_total_interviews = int(existing[4] or 0)
            existing_departments = json.loads(existing[5]) if existing[5] else []
            existing_tasks = json.loads(existing[6]) if existing[6] else []
            existing_use_cases = json.loads(existing[7]) if existing[7] else []
            existing_validated = json.loads(existing[8]) if existing[8] else []
            existing_themes = json.loads(existing[9]) if existing[9] else []
            existing_contributor_keys = json.loads(existing[11]) if existing[11] else []
            merged_contributor_keys = _coerce_contributor_keys(existing_contributor_keys + ([contributor_key] if contributor_key else []))
            total_sessions = int(existing[12] if existing[12] is not None else existing_total_interviews) + 1
            distinct_contributor_count = len(merged_contributor_keys) if merged_contributor_keys else total_sessions

            merged_company = _merge_company_display_name(existing[1], company_display)
            if merged_company and merged_company != (existing[1] or ""):
                updates.append("company = ?")
                params.append(merged_company)
            if company_key and company_key != (existing[2] or ""):
                updates.append("company_key = ?")
                params.append(company_key)

            merged_north_star = _merge_north_star(existing[3] if existing else "", north_star)
            if merged_north_star and merged_north_star != (existing[3] if existing else ""):
                updates.append("north_star = ?")
                params.append(merged_north_star)

            if merged_department:
                merged_departments = _merge_unique_strings(existing_departments, [merged_department])
                updates.append("departments = ?")
                params.append(json.dumps(merged_departments))

            if tasks:
                updates.append("all_tasks = ?")
                params.append(json.dumps(_merge_aggregated_tasks(existing_tasks, tasks, contributor_key=contributor_key)))

            if use_cases:
                updates.append("all_use_cases = ?")
                params.append(json.dumps(_merge_aggregated_use_cases(existing_use_cases, use_cases, contributor_key=contributor_key)))

            if validated_use_cases:
                updates.append("validated_use_cases = ?")
                params.append(json.dumps(_merge_validated_use_case_feedback(existing_validated, validated_use_cases)))
            if recurring_themes:
                updates.append("recurring_themes = ?")
                params.append(json.dumps(_merge_recurring_themes(existing_themes, recurring_themes, contributor_key=contributor_key)))

            updates.append("total_interviews = ?")
            params.append(distinct_contributor_count)
            updates.append("contributor_keys = ?")
            params.append(json.dumps(merged_contributor_keys))
            updates.append("total_sessions = ?")
            params.append(total_sessions)
            updates.append("last_updated = ?")
            params.append(datetime.now().isoformat())

            params.append(existing[0])

            c.execute(f"UPDATE company_insights SET {', '.join(updates)} WHERE id = ?", params)
        else:
            c.execute(
                """INSERT INTO company_insights 
                         (company, company_key, north_star, total_interviews, departments, all_tasks, all_use_cases, validated_use_cases, recurring_themes, contributor_keys, total_sessions)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    company_display,
                    company_key,
                    _merge_north_star("", north_star),
                    1 if contributor_key else 0,
                    json.dumps(_merge_unique_strings([], [merged_department])),
                    json.dumps(_merge_aggregated_tasks([], tasks, contributor_key=contributor_key)) if tasks else "[]",
                    json.dumps(_merge_aggregated_use_cases([], use_cases, contributor_key=contributor_key)) if use_cases else "[]",
                    json.dumps(validated_use_cases) if validated_use_cases else "[]",
                    json.dumps(_merge_recurring_themes([], recurring_themes, contributor_key=contributor_key)) if recurring_themes else "[]",
                    json.dumps([contributor_key] if contributor_key else []),
                    1,
                ),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_company_insights(company: str) -> Optional[Dict]:
    company_key = _normalize_company_key(company)
    if _is_mongo_enabled():
        client, _, insights_col = _mongo_collections()
        try:
            doc = insights_col.find_one(_mongo_company_match_query(company))
            if not doc:
                return None
            return {
                "company": doc.get("company"),
                "company_key": doc.get("company_key"),
                "north_star": doc.get("north_star", ""),
                "total_interviews": doc.get("total_interviews", 0),
                "total_sessions": doc.get("total_sessions", doc.get("total_interviews", 0)),
                "departments": doc.get("departments", []),
                "all_tasks": doc.get("all_tasks", []),
                "all_use_cases": doc.get("all_use_cases", []),
                "validated_use_cases": doc.get("validated_use_cases", []),
                "recurring_themes": doc.get("recurring_themes", []),
                "contributor_keys": doc.get("contributor_keys", []),
                "last_updated": doc.get("last_updated"),
            }
        finally:
            client.close()

    conn = _sqlite_connect()
    c = conn.cursor()
    c.execute(
        """SELECT company, company_key, north_star, total_interviews, departments, all_tasks, all_use_cases,
                  validated_use_cases, recurring_themes, last_updated, contributor_keys, total_sessions
           FROM company_insights
           WHERE company_key = ? OR lower(trim(company)) = ?
           ORDER BY CASE WHEN company_key = ? THEN 0 ELSE 1 END, id ASC
           LIMIT 1""",
        (company_key, company_key, company_key),
    )
    row = c.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "company": row[0],
        "company_key": row[1],
        "north_star": row[2],
        "total_interviews": row[3],
        "departments": json.loads(row[4]) if row[4] else [],
        "all_tasks": json.loads(row[5]) if row[5] else [],
        "all_use_cases": json.loads(row[6]) if row[6] else [],
        "validated_use_cases": json.loads(row[7]) if row[7] else [],
        "recurring_themes": json.loads(row[8]) if row[8] else [],
        "last_updated": row[9],
        "contributor_keys": json.loads(row[10]) if row[10] else [],
        "total_sessions": row[11] if row[11] is not None else row[3],
    }


def save_interview_checkpoint(draft_id: str, payload: Dict) -> None:
    if not draft_id:
        return

    if _is_mongo_enabled():
        client, drafts_col = _mongo_drafts_collection()
        try:
            drafts_col.replace_one(
                {"draft_id": draft_id},
                {"draft_id": draft_id, "payload": payload, "updated_at": datetime.utcnow()},
                upsert=True,
            )
        finally:
            client.close()
        return

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS interview_drafts
                 (draft_id TEXT PRIMARY KEY,
                  payload TEXT,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        """INSERT INTO interview_drafts (draft_id, payload, updated_at)
           VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(draft_id) DO UPDATE SET
               payload = excluded.payload,
               updated_at = CURRENT_TIMESTAMP""",
        (draft_id, json.dumps(payload)),
    )
    conn.commit()
    conn.close()


def get_interview_checkpoint(draft_id: str) -> Optional[Dict]:
    if not draft_id:
        return None

    if _is_mongo_enabled():
        client, drafts_col = _mongo_drafts_collection()
        try:
            doc = drafts_col.find_one({"draft_id": draft_id})
            if not doc:
                return None
            return doc.get("payload")
        finally:
            client.close()

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS interview_drafts
                 (draft_id TEXT PRIMARY KEY,
                  payload TEXT,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute("SELECT payload FROM interview_drafts WHERE draft_id = ?", (draft_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row[0]) if row[0] else None


def delete_interview_checkpoint(draft_id: str) -> None:
    if not draft_id:
        return

    if _is_mongo_enabled():
        client, drafts_col = _mongo_drafts_collection()
        try:
            drafts_col.delete_one({"draft_id": draft_id})
        finally:
            client.close()
        return

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS interview_drafts
                 (draft_id TEXT PRIMARY KEY,
                  payload TEXT,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute("DELETE FROM interview_drafts WHERE draft_id = ?", (draft_id,))
    conn.commit()
    conn.close()


def delete_interview_checkpoints_for_session(session_id: str, owner_fingerprint: Optional[str] = None) -> None:
    session_id = str(session_id or "").strip()
    owner_fingerprint = str(owner_fingerprint or "").strip()
    if not session_id:
        return

    if _is_mongo_enabled():
        client, drafts_col = _mongo_drafts_collection()
        try:
            query = {"payload.state.session_id": session_id}
            if owner_fingerprint:
                query["payload.state.owner_fingerprint"] = owner_fingerprint
            drafts_col.delete_many(query)
        finally:
            client.close()
        return

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS interview_drafts
                 (draft_id TEXT PRIMARY KEY,
                  payload TEXT,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute("SELECT draft_id, payload FROM interview_drafts")
    draft_ids_to_delete = []
    for draft_id, payload_raw in c.fetchall():
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except Exception:
            continue
        state = payload.get("state", {}) if isinstance(payload, dict) else {}
        if str(state.get("session_id", "") or "").strip() != session_id:
            continue
        if owner_fingerprint and str(state.get("owner_fingerprint", "") or "").strip() != owner_fingerprint:
            continue
        draft_ids_to_delete.append(draft_id)

    if draft_ids_to_delete:
        c.executemany("DELETE FROM interview_drafts WHERE draft_id = ?", [(draft_id,) for draft_id in draft_ids_to_delete])
    conn.commit()
    conn.close()


def delete_open_interview_checkpoints_for_owner(owner_fingerprint: str) -> int:
    owner_fingerprint = str(owner_fingerprint or "").strip()
    if not owner_fingerprint:
        return 0

    if _is_mongo_enabled():
        client, drafts_col = _mongo_drafts_collection()
        try:
            result = drafts_col.delete_many(
                {
                    "payload.state.owner_fingerprint": owner_fingerprint,
                    "payload.state.report_done": {"$ne": True},
                }
            )
            return int(getattr(result, "deleted_count", 0) or 0)
        finally:
            client.close()

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS interview_drafts
                 (draft_id TEXT PRIMARY KEY,
                  payload TEXT,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute("SELECT draft_id, payload FROM interview_drafts")
    draft_ids_to_delete = []
    owner_prefix = f"{owner_fingerprint}:"
    for draft_id, payload_raw in c.fetchall():
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except Exception:
            continue
        state = payload.get("state", {}) if isinstance(payload, dict) else {}
        if bool(state.get("report_done")):
            continue
        state_owner = str(state.get("owner_fingerprint", "") or "").strip()
        if state_owner == owner_fingerprint or str(draft_id or "").startswith(owner_prefix):
            draft_ids_to_delete.append(draft_id)

    if draft_ids_to_delete:
        c.executemany("DELETE FROM interview_drafts WHERE draft_id = ?", [(draft_id,) for draft_id in draft_ids_to_delete])
    conn.commit()
    conn.close()
    return len(draft_ids_to_delete)


def get_open_interview_checkpoints(limit: int = 20, owner_fingerprint: Optional[str] = None) -> List[Dict]:
    if _is_mongo_enabled():
        client, drafts_col = _mongo_drafts_collection()
        try:
            docs = list(drafts_col.find({}).sort("updated_at", -1).limit(max(1, int(limit))))
            out: List[Dict] = []
            for d in docs:
                payload = d.get("payload") if isinstance(d, dict) else None
                state = payload.get("state", {}) if isinstance(payload, dict) else {}
                if not bool(state.get("report_done")):
                    if owner_fingerprint and str(state.get("owner_fingerprint", "")) != str(owner_fingerprint):
                        continue
                    out.append(
                        {
                            "draft_id": d.get("draft_id"),
                            "payload": payload,
                            "updated_at": d.get("updated_at"),
                        }
                    )
            return out
        finally:
            client.close()

    conn = sqlite3.connect(SQLITE_DB_PATH)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS interview_drafts
                 (draft_id TEXT PRIMARY KEY,
                  payload TEXT,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    c.execute(
        "SELECT draft_id, payload, updated_at FROM interview_drafts ORDER BY updated_at DESC LIMIT ?",
        (max(1, int(limit)),),
    )
    rows = c.fetchall()
    conn.close()

    out: List[Dict] = []
    for draft_id, payload_raw, updated_at in rows:
        payload = json.loads(payload_raw) if payload_raw else {}
        state = payload.get("state", {}) if isinstance(payload, dict) else {}
        if not bool(state.get("report_done")):
            if owner_fingerprint and str(state.get("owner_fingerprint", "")) != str(owner_fingerprint):
                continue
            out.append(
                {
                    "draft_id": draft_id,
                    "payload": payload,
                    "updated_at": updated_at,
                }
            )
    return out
