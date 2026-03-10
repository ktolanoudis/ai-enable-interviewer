import os
from typing import Dict
from urllib.parse import urlparse

import boto3
from botocore.config import Config

def _clean_env(name: str, default: str = "") -> str:
    """
    Normalize environment values copied from UI/forms:
    - trim whitespace/newlines
    - remove wrapping single/double quotes
    """
    raw = os.getenv(name, default)
    if raw is None:
        return default
    value = raw.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1].strip()
    return value


def _normalize_endpoint(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        return value
    parsed = urlparse(value)
    if not parsed.scheme:
        value = f"https://{value}"
    return value


STORAGE_BACKEND = os.getenv("REPORT_STORAGE_BACKEND", "local").strip().lower()
S3_BUCKET = _clean_env("S3_BUCKET")
S3_ENDPOINT_URL = _normalize_endpoint(_clean_env("S3_ENDPOINT_URL"))
S3_REGION = _clean_env("S3_REGION", "us-east-1")
S3_KEY_ID = _clean_env("S3_ACCESS_KEY_ID")
S3_SECRET = _clean_env("S3_SECRET_ACCESS_KEY")
REPORTS_PREFIX = os.getenv("REPORTS_PREFIX", "reports").strip("/") or "reports"
LOCAL_REPORTS_DIR = os.getenv("LOCAL_REPORTS_DIR", "reports")
DISABLE_LOCAL_REPORTS = os.getenv("DISABLE_LOCAL_REPORTS", "").strip().lower() in {"1", "true", "yes", "on"}
S3_PUBLIC_BASE_URL = _clean_env("S3_PUBLIC_BASE_URL").rstrip("/")


def _s3_enabled() -> bool:
    return (
        STORAGE_BACKEND == "s3"
        and bool(S3_BUCKET)
        and bool(S3_ENDPOINT_URL)
        and bool(S3_KEY_ID)
        and bool(S3_SECRET)
    )


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        region_name=S3_REGION,
        aws_access_key_id=S3_KEY_ID,
        aws_secret_access_key=S3_SECRET,
        config=Config(signature_version="s3v4"),
    )


def _build_public_url(key: str) -> str:
    if S3_PUBLIC_BASE_URL:
        return f"{S3_PUBLIC_BASE_URL}/{key}"
    return f"{S3_ENDPOINT_URL}/{S3_BUCKET}/{key}"


def persist_report_files(session_id: str, report_json: str, report_md: str) -> Dict[str, str]:
    """
    Persist report files to local disk and/or S3-compatible object storage.
    Returns locations for display/logging.
    """
    json_name = f"report_{session_id}.json"
    md_name = f"report_{session_id}.md"
    result = {"json_path": "", "md_path": "", "json_url": "", "md_url": ""}

    if not DISABLE_LOCAL_REPORTS:
        os.makedirs(LOCAL_REPORTS_DIR, exist_ok=True)
        json_path = os.path.join(LOCAL_REPORTS_DIR, json_name)
        md_path = os.path.join(LOCAL_REPORTS_DIR, md_name)
        with open(json_path, "w") as f:
            f.write(report_json)
        with open(md_path, "w") as f:
            f.write(report_md)
        result["json_path"] = json_path
        result["md_path"] = md_path

    if _s3_enabled():
        json_key = f"{REPORTS_PREFIX}/{json_name}"
        md_key = f"{REPORTS_PREFIX}/{md_name}"
        client = _s3_client()
        client.put_object(Bucket=S3_BUCKET, Key=json_key, Body=report_json.encode("utf-8"), ContentType="application/json")
        client.put_object(Bucket=S3_BUCKET, Key=md_key, Body=report_md.encode("utf-8"), ContentType="text/markdown")
        result["json_url"] = _build_public_url(json_key)
        result["md_url"] = _build_public_url(md_key)

    return result
