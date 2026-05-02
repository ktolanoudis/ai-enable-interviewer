import concurrent.futures
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402


class CompanyMemoryConcurrencyTests(unittest.TestCase):
    def test_sqlite_company_insights_updates_preserve_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "sessions.db")
            with patch.object(db, "DB_BACKEND", "sqlite"), patch.object(db, "SQLITE_DB_PATH", db_path), patch.object(
                db,
                "SQLITE_BUSY_TIMEOUT_MS",
                5000,
            ):
                db.init_db()

                def write_update(index: int):
                    db.update_company_insights(
                        company="Concurrent Co",
                        department=f"Dept {index}",
                        tasks=[
                            {
                                "name": f"Review item {index}",
                                "description": f"Review item {index}",
                                "department": f"Dept {index}",
                                "friction_points": [f"Delay {index}"],
                                "current_systems": ["Spreadsheet"],
                                "manual_steps": ["Copy data"],
                            }
                        ],
                        contributor={"contributor_key": f"contrib_{index}"},
                    )

                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    list(executor.map(write_update, range(6)))

                insights = db.get_company_insights("Concurrent Co")

        self.assertEqual(insights["total_sessions"], 6)
        self.assertEqual(insights["total_interviews"], 6)
        self.assertEqual(len(insights["contributor_keys"]), 6)
        self.assertEqual(len(insights["all_tasks"]), 6)


if __name__ == "__main__":
    unittest.main()
