import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402


class CheckpointCleanupTests(unittest.TestCase):
    def test_save_session_persists_contributor_key_for_survey_join(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "sessions.db")
            with patch.object(db, "DB_BACKEND", "sqlite"), patch.object(db, "SQLITE_DB_PATH", db_path):
                db.init_db()
                db.save_session(
                    company="Acme",
                    employee="Ana",
                    department="Finance",
                    role="Analyst",
                    seniority_level="intermediate",
                    transcript="transcript",
                    report_json="{}",
                    report_md="# Report",
                    contributor={"contributor_key": "contrib_123"},
                )

                conn = sqlite3.connect(db_path)
                try:
                    row = conn.execute("SELECT contributor_key FROM sessions").fetchone()
                finally:
                    conn.close()

        self.assertEqual(row[0], "contrib_123")

    def test_delete_interview_checkpoints_for_session_only_removes_matching_owner_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "sessions.db")
            with patch.object(db, "DB_BACKEND", "sqlite"), patch.object(db, "SQLITE_DB_PATH", db_path):
                db.init_db()
                db.save_interview_checkpoint(
                    "owner_a:draft_1",
                    {"state": {"session_id": "session_1", "owner_fingerprint": "owner_a", "report_done": False}},
                )
                db.save_interview_checkpoint(
                    "owner_a:draft_2",
                    {"state": {"session_id": "session_1", "owner_fingerprint": "owner_a", "report_done": False}},
                )
                db.save_interview_checkpoint(
                    "owner_b:draft_3",
                    {"state": {"session_id": "session_1", "owner_fingerprint": "owner_b", "report_done": False}},
                )
                db.save_interview_checkpoint(
                    "owner_a:draft_4",
                    {"state": {"session_id": "session_2", "owner_fingerprint": "owner_a", "report_done": False}},
                )

                db.delete_interview_checkpoints_for_session("session_1", owner_fingerprint="owner_a")

                remaining = db.get_open_interview_checkpoints(limit=10)

        remaining_ids = {item["draft_id"] for item in remaining}
        self.assertEqual(remaining_ids, {"owner_b:draft_3", "owner_a:draft_4"})

    def test_delete_open_interview_checkpoints_for_owner_preserves_completed_and_other_owners(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "sessions.db")
            with patch.object(db, "DB_BACKEND", "sqlite"), patch.object(db, "SQLITE_DB_PATH", db_path):
                db.init_db()
                db.save_interview_checkpoint(
                    "owner_a:draft_open_1",
                    {"state": {"session_id": "session_1", "owner_fingerprint": "owner_a", "report_done": False}},
                )
                db.save_interview_checkpoint(
                    "owner_a:draft_open_legacy",
                    {"state": {"session_id": "session_2", "report_done": False}},
                )
                db.save_interview_checkpoint(
                    "owner_a:draft_complete",
                    {"state": {"session_id": "session_3", "owner_fingerprint": "owner_a", "report_done": True}},
                )
                db.save_interview_checkpoint(
                    "owner_b:draft_open_2",
                    {"state": {"session_id": "session_4", "owner_fingerprint": "owner_b", "report_done": False}},
                )

                deleted_count = db.delete_open_interview_checkpoints_for_owner("owner_a")
                remaining = db.get_open_interview_checkpoints(limit=10)
                completed = db.get_interview_checkpoint("owner_a:draft_complete")

        remaining_ids = {item["draft_id"] for item in remaining}
        self.assertEqual(deleted_count, 2)
        self.assertEqual(remaining_ids, {"owner_b:draft_open_2"})
        self.assertIsNotNone(completed)


if __name__ == "__main__":
    unittest.main()
