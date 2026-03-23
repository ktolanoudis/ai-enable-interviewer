import datetime
import hashlib
import traceback
from http.cookies import SimpleCookie

import chainlit as cl

from db import (
    delete_interview_checkpoint,
    get_open_interview_checkpoints,
    save_interview_checkpoint,
)
from session_state import CHECKPOINT_STATE_KEYS


def detect_thread_id(message: cl.Message = None, thread: dict = None) -> str:
    if isinstance(thread, dict):
        for key in ("id", "thread_id"):
            val = thread.get(key)
            if val:
                return str(val)

    if message is not None:
        msg_thread_id = getattr(message, "thread_id", None)
        if msg_thread_id:
            return str(msg_thread_id)

    session_thread = cl.user_session.get("thread_id")
    if session_thread:
        return str(session_thread)

    try:
        current = getattr(getattr(cl, "context", None), "session", None)
        thread_val = getattr(current, "thread_id", None)
        if thread_val:
            return str(thread_val)
    except Exception:
        pass
    return ""


def detect_client_session_id() -> str:
    try:
        current = getattr(getattr(cl, "context", None), "session", None)
        sid = getattr(current, "id", None)
        if sid:
            return str(sid)
    except Exception:
        pass
    return ""


def _build_owner(raw: str) -> str:
    digest = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:24]
    return f"owner_{digest}"


def detect_owner_identity() -> tuple[str, str]:
    user_obj = cl.user_session.get("user")
    if isinstance(user_obj, dict):
        for key in ("id", "identifier", "email", "name"):
            value = user_obj.get(key)
            if value:
                return "user", _build_owner(f"user:{value}")
    elif user_obj is not None:
        for key in ("id", "identifier", "email", "name"):
            value = getattr(user_obj, key, None)
            if value:
                return "user", _build_owner(f"user:{value}")

    try:
        current = getattr(getattr(cl, "context", None), "session", None)
        current_user = getattr(current, "user", None)
        if current_user is not None:
            for key in ("id", "identifier", "email", "name"):
                value = getattr(current_user, key, None)
                if value:
                    return "user", _build_owner(f"user:{value}")

        cookie_header = None
        environ = getattr(current, "environ", None)
        if isinstance(environ, dict):
            cookie_header = environ.get("HTTP_COOKIE") or environ.get("cookie")
        if not cookie_header:
            headers = getattr(current, "headers", None)
            if headers is not None:
                getter = getattr(headers, "get", None)
                if callable(getter):
                    cookie_header = getter("cookie") or getter("Cookie")
                elif isinstance(headers, dict):
                    cookie_header = headers.get("cookie") or headers.get("Cookie")
        if cookie_header:
            try:
                cookie = SimpleCookie()
                cookie.load(str(cookie_header))
                client_cookie = cookie.get("ai_enable_client_id")
                if client_cookie and client_cookie.value:
                    return "cookie", _build_owner(f"anon_client:{client_cookie.value}")
            except Exception:
                pass

        session_id = getattr(current, "id", None)
        if session_id:
            return "chainlit_session", _build_owner(f"chainlit_session:{session_id}")
    except Exception:
        pass

    raw = f"ephemeral:{detect_client_session_id() or datetime.datetime.utcnow().isoformat()}"
    return "ephemeral", _build_owner(raw)


def _owner_source_rank(source: str) -> int:
    return {
        "user": 3,
        "cookie": 2,
        "chainlit_session": 1,
        "ephemeral": 0,
    }.get(str(source or "").strip().lower(), 0)


def ensure_owner_fingerprint() -> str:
    existing = cl.user_session.get("owner_fingerprint")
    existing_source = cl.user_session.get("owner_identity_source")
    current_source, owner = detect_owner_identity()
    if existing and _owner_source_rank(existing_source) >= _owner_source_rank(current_source):
        return str(existing)
    cl.user_session.set("owner_fingerprint", owner)
    cl.user_session.set("owner_identity_source", current_source)
    return owner


def _unscoped_draft_id(value) -> str:
    raw = str(value or "").strip()
    if raw.startswith("owner_") and ":" in raw:
        return raw.split(":", 1)[1]
    return raw


def active_draft_id(message: cl.Message = None, thread: dict = None) -> str:
    owner = ensure_owner_fingerprint()
    thread_id = detect_thread_id(message=message, thread=thread)
    if thread_id:
        cl.user_session.set("thread_id", thread_id)
    session_id = cl.user_session.get("session_id")
    existing_draft_id = _unscoped_draft_id(cl.user_session.get("active_draft_id"))
    draft_id = thread_id or existing_draft_id or session_id
    if draft_id:
        scoped_id = f"{owner}:{draft_id}"
        cl.user_session.set("active_draft_id", scoped_id)
        return scoped_id
    return ""


def checkpoint_payload() -> dict:
    state = {}
    for key in CHECKPOINT_STATE_KEYS:
        state[key] = cl.user_session.get(key)
    return {"saved_at": datetime.datetime.utcnow().isoformat(), "state": state}


def save_checkpoint(message: cl.Message = None, thread: dict = None) -> None:
    draft_id = active_draft_id(message=message, thread=thread)
    if not draft_id:
        return
    messages = cl.user_session.get("messages") or []
    has_user_message = any(str(m.get("role", "")).strip().lower() == "user" for m in messages if isinstance(m, dict))
    if not has_user_message:
        return
    try:
        if cl.user_session.get("report_done") and cl.user_session.get("collection_step") == "__closed__":
            delete_interview_checkpoint(draft_id)
            return
        save_interview_checkpoint(draft_id, checkpoint_payload())
    except Exception:
        traceback.print_exc()


def restore_checkpoint_to_session(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    state = payload.get("state")
    if not isinstance(state, dict):
        return False
    for key in CHECKPOINT_STATE_KEYS:
        if key in state:
            cl.user_session.set(key, state.get(key))
    return True


async def replay_messages(messages: list) -> None:
    if not isinstance(messages, list):
        return
    for message in messages:
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if role == "assistant":
            author = "Interviewer"
        elif role == "user":
            author = "User"
        else:
            author = role.capitalize() if role else "Interviewer"
        await cl.Message(content=content, author=author).send()


def _draft_matches_thread(draft: dict, thread_id: str) -> bool:
    if not thread_id:
        return False
    draft_id = str((draft or {}).get("draft_id") or "").strip()
    if not draft_id:
        return False
    if draft_id == thread_id:
        return True
    return draft_id.endswith(f":{thread_id}")


def fallback_open_draft_checkpoint(owner: str):
    checkpoint = None
    thread_id = detect_thread_id()
    open_drafts = get_open_interview_checkpoints(limit=20, owner_fingerprint=owner)
    now_ts = datetime.datetime.now().timestamp()
    recent = [d for d in open_drafts if now_ts - _to_epoch_seconds(d.get("updated_at")) <= 1800]
    if len(recent) == 1:
        checkpoint = recent[0].get("payload")
        restored_draft_id = str(recent[0].get("draft_id") or "")
        if restored_draft_id:
            cl.user_session.set("active_draft_id", restored_draft_id)
    elif recent:
        latest_recent = sorted(recent, key=lambda d: _to_epoch_seconds(d.get("updated_at")), reverse=True)[0]
        checkpoint = latest_recent.get("payload")
        restored_draft_id = str(latest_recent.get("draft_id") or "")
        if restored_draft_id:
            cl.user_session.set("active_draft_id", restored_draft_id)
    elif open_drafts:
        latest = sorted(open_drafts, key=lambda d: _to_epoch_seconds(d.get("updated_at")), reverse=True)[0]
        checkpoint = latest.get("payload")
        restored_draft_id = str(latest.get("draft_id") or "")
        if restored_draft_id:
            cl.user_session.set("active_draft_id", restored_draft_id)

    if checkpoint or not thread_id:
        return checkpoint

    global_open_drafts = get_open_interview_checkpoints(limit=100)
    thread_matches = [d for d in global_open_drafts if _draft_matches_thread(d, thread_id)]
    if not thread_matches:
        return None

    latest_thread_match = sorted(
        thread_matches,
        key=lambda d: _to_epoch_seconds(d.get("updated_at")),
        reverse=True,
    )[0]
    checkpoint = latest_thread_match.get("payload")
    restored_draft_id = str(latest_thread_match.get("draft_id") or "")
    if restored_draft_id:
        cl.user_session.set("active_draft_id", restored_draft_id)
    return checkpoint


def _to_epoch_seconds(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.timestamp()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return 0.0
        if " " in raw and "T" not in raw:
            raw = raw.replace(" ", "T")
        try:
            return datetime.datetime.fromisoformat(raw).timestamp()
        except Exception:
            return 0.0
    return 0.0
