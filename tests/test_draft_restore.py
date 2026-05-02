import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_support import install_import_stubs

install_import_stubs()

os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("SQLITE_DB_PATH", "/tmp/discovery_test_sessions.db")
os.environ.setdefault("LOCAL_REPORTS_DIR", "/tmp/discovery_test_reports")

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import chainlit_app  # noqa: E402


class _FakeUserSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class DraftRestoreTests(unittest.TestCase):
    def test_restore_suppression_cookie_requires_non_empty_value(self):
        fake_chainlit = types.SimpleNamespace(
            context=types.SimpleNamespace(
                session=types.SimpleNamespace(environ={"HTTP_COOKIE": "foo=bar; suppress_draft_restore=abc123"})
            )
        )
        with patch.object(chainlit_app, "cl", fake_chainlit):
            self.assertTrue(chainlit_app._has_restore_suppression_cookie())

        fake_chainlit.context.session.environ = {"HTTP_COOKIE": "foo=bar; suppress_draft_restore="}
        with patch.object(chainlit_app, "cl", fake_chainlit):
            self.assertFalse(chainlit_app._has_restore_suppression_cookie())

    def test_new_chat_suppression_clears_stale_draft_before_restore_lookup(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("chat_start_handled", False)
        fake_chainlit.user_session.set("active_draft_id", "owner_prev:stale_draft")
        fake_chainlit.user_session.set("checkpoint_restored_this_connection", True)
        fake_chainlit.user_session.set("messages", [{"role": "user", "content": "old draft"}])
        fake_chainlit.user_session.set("owner_fingerprint", "owner_prev")
        fake_chainlit.user_session.set("owner_identity_source", "cookie")

        restore_calls = []

        async def _fake_restore_checkpoint_if_available(draft_id="", owner="", allow_fallback=False):
            restore_calls.append(
                {
                    "draft_id": draft_id,
                    "owner": owner,
                    "allow_fallback": allow_fallback,
                }
            )
            return False

        def _fake_init_session_state():
            fake_chainlit.user_session.set("collection_step", "name")
            fake_chainlit.user_session.set("metadata", {})
            fake_chainlit.user_session.set("messages", [])
            fake_chainlit.user_session.set("notes", {"missing": [], "ready_for_report": False})
            fake_chainlit.user_session.set("report_done", False)
            fake_chainlit.user_session.set("framework_step", None)
            fake_chainlit.user_session.set("session_id", "fresh_session")
            fake_chainlit.user_session.set("active_draft_id", None)
            fake_chainlit.user_session.set("thread_id", None)
            fake_chainlit.user_session.set("interview_started", False)
            fake_chainlit.user_session.set("welcome_sent", False)
            fake_chainlit.user_session.set("chat_start_handled", False)

        def _fake_ensure_owner_fingerprint():
            return str(fake_chainlit.user_session.get("owner_fingerprint") or "owner_prev")

        def _fake_active_draft_id(*args, **kwargs):
            owner = _fake_ensure_owner_fingerprint()
            existing = fake_chainlit.user_session.get("active_draft_id")
            if existing:
                return str(existing)
            session_id = fake_chainlit.user_session.get("session_id") or "fresh_session"
            scoped = f"{owner}:{session_id}"
            fake_chainlit.user_session.set("active_draft_id", scoped)
            return scoped

        with patch.object(chainlit_app, "cl", fake_chainlit), patch.object(
            chainlit_app,
            "_has_restore_suppression_cookie",
            return_value=True,
        ), patch.object(
            chainlit_app,
            "delete_open_interview_checkpoints_for_owner",
            return_value=1,
        ), patch.object(
            chainlit_app,
            "_restore_checkpoint_if_available",
            side_effect=_fake_restore_checkpoint_if_available,
        ), patch.object(
            chainlit_app,
            "send_welcome_prompt",
            AsyncMock(),
        ), patch.object(
            chainlit_app,
            "init_session_state",
            side_effect=_fake_init_session_state,
        ), patch.object(
            chainlit_app,
            "ensure_owner_fingerprint",
            side_effect=_fake_ensure_owner_fingerprint,
        ), patch.object(
            chainlit_app,
            "active_draft_id",
            side_effect=_fake_active_draft_id,
        ), patch.object(
            chainlit_app,
            "save_checkpoint",
            lambda *args, **kwargs: None,
        ):
            asyncio.run(chainlit_app.start())

        self.assertEqual(len(restore_calls), 1)
        self.assertFalse(restore_calls[0]["allow_fallback"])
        self.assertNotEqual(restore_calls[0]["draft_id"], "owner_prev:stale_draft")
        self.assertNotEqual(fake_chainlit.user_session.get("active_draft_id"), "owner_prev:stale_draft")
        self.assertEqual(fake_chainlit.user_session.get("owner_fingerprint"), "owner_prev")
        self.assertFalse(fake_chainlit.user_session.get("checkpoint_restored_this_connection"))

    def test_new_chat_suppression_prevents_resume_restore(self):
        fake_chainlit = types.SimpleNamespace(user_session=_FakeUserSession())
        fake_chainlit.user_session.set("active_draft_id", "owner_prev:stale_draft")
        fake_chainlit.user_session.set("checkpoint_restored_this_connection", True)
        fake_chainlit.user_session.set("messages", [{"role": "user", "content": "old draft"}])
        fake_chainlit.user_session.set("owner_fingerprint", "owner_prev")
        fake_chainlit.user_session.set("owner_identity_source", "cookie")

        restore_checkpoint = AsyncMock(return_value=True)

        def _fake_init_session_state():
            fake_chainlit.user_session.set("collection_step", "name")
            fake_chainlit.user_session.set("metadata", {})
            fake_chainlit.user_session.set("messages", [])
            fake_chainlit.user_session.set("notes", {"missing": [], "ready_for_report": False})
            fake_chainlit.user_session.set("report_done", False)
            fake_chainlit.user_session.set("framework_step", None)
            fake_chainlit.user_session.set("session_id", "fresh_session")
            fake_chainlit.user_session.set("active_draft_id", None)
            fake_chainlit.user_session.set("thread_id", None)
            fake_chainlit.user_session.set("interview_started", False)
            fake_chainlit.user_session.set("welcome_sent", False)
            fake_chainlit.user_session.set("chat_start_handled", False)

        with patch.object(chainlit_app, "cl", fake_chainlit), patch.object(
            chainlit_app,
            "_has_restore_suppression_cookie",
            return_value=True,
        ), patch.object(
            chainlit_app,
            "delete_open_interview_checkpoints_for_owner",
            return_value=1,
        ), patch.object(
            chainlit_app,
            "_restore_checkpoint_if_available",
            restore_checkpoint,
        ), patch.object(
            chainlit_app,
            "thread_is_completed",
            return_value=False,
        ), patch.object(
            chainlit_app,
            "send_welcome_prompt",
            AsyncMock(),
        ), patch.object(
            chainlit_app,
            "init_session_state",
            side_effect=_fake_init_session_state,
        ), patch.object(
            chainlit_app,
            "ensure_owner_fingerprint",
            return_value="owner_prev",
        ):
            asyncio.run(chainlit_app.resume({"id": "stale_draft"}))

        restore_checkpoint.assert_not_awaited()
        self.assertEqual(fake_chainlit.user_session.get("messages"), [{"role": "assistant", "content": chainlit_app.WELCOME_TEXT}])
        self.assertIsNone(fake_chainlit.user_session.get("active_draft_id"))
        self.assertTrue(fake_chainlit.user_session.get("chat_start_handled"))
        self.assertFalse(fake_chainlit.user_session.get("checkpoint_restored_this_connection"))


if __name__ == "__main__":
    unittest.main()
