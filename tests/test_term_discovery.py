import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_support import install_import_stubs

install_import_stubs()

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import term_discovery  # noqa: E402


class TermDiscoveryTests(unittest.TestCase):
    def test_lookup_searches_public_term_before_company_scoped_query(self):
        serp_calls = []

        def fake_serp(term, company=""):
            serp_calls.append((term, company))
            return "generic public snippet" if company == "" else None

        with patch.object(term_discovery, "search_term_with_serpapi", side_effect=fake_serp), patch.object(
            term_discovery,
            "search_term_with_ddg",
            return_value=None,
        ), patch.object(
            term_discovery,
            "synthesize_term_context",
            side_effect=lambda term, company, snippet: f"{company}:{snippet}",
        ), patch.object(
            term_discovery,
            "infer_public_term_context",
            return_value=None,
        ):
            context = term_discovery.lookup_term_context("SomeProduct", "TelecomCo")

        self.assertEqual(context, ":generic public snippet")
        self.assertEqual(serp_calls, [("SomeProduct", "")])

    def test_lookup_retries_with_company_when_public_search_misses(self):
        serp_calls = []

        def fake_serp(term, company=""):
            serp_calls.append((term, company))
            return "company-scoped snippet" if company == "TelecomCo" else None

        with patch.object(term_discovery, "search_term_with_serpapi", side_effect=fake_serp), patch.object(
            term_discovery,
            "search_term_with_ddg",
            return_value=None,
        ), patch.object(
            term_discovery,
            "synthesize_term_context",
            side_effect=lambda term, company, snippet: f"{company}:{snippet}",
        ), patch.object(
            term_discovery,
            "infer_public_term_context",
            return_value=None,
        ):
            context = term_discovery.lookup_term_context("SomeProduct", "TelecomCo")

        self.assertEqual(context, "TelecomCo:company-scoped snippet")
        self.assertEqual(serp_calls, [("SomeProduct", ""), ("SomeProduct", "TelecomCo")])


if __name__ == "__main__":
    unittest.main()
