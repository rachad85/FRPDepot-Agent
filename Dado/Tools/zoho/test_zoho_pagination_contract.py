from __future__ import annotations

from pathlib import Path
import unittest

import zoho_tool


ROOT = Path(__file__).resolve().parent


class PaginationMetadataContractTests(unittest.TestCase):
    def test_returns_explicit_boolean_values(self) -> None:
        self.assertTrue(
            zoho_tool.require_has_more_page(
                {"page_context": {"has_more_page": True}}, "/test", 1
            )
        )
        self.assertFalse(
            zoho_tool.require_has_more_page(
                {"page_context": {"has_more_page": False}}, "/test", 2
            )
        )

    def test_refuses_missing_context(self) -> None:
        with self.assertRaisesRegex(zoho_tool.ZohoError, "refusing a partial result"):
            zoho_tool.require_has_more_page({}, "/test", 1)

    def test_refuses_empty_context(self) -> None:
        with self.assertRaisesRegex(zoho_tool.ZohoError, "boolean has_more_page"):
            zoho_tool.require_has_more_page({"page_context": {}}, "/test", 1)

    def test_refuses_non_boolean_answer(self) -> None:
        for value in (0, 1, "false", "true", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(zoho_tool.ZohoError, "boolean has_more_page"):
                    zoho_tool.require_has_more_page(
                        {"page_context": {"has_more_page": value}}, "/test", 1
                    )

    def test_preserves_callers_error_type(self) -> None:
        class CallerError(RuntimeError):
            pass

        with self.assertRaises(CallerError):
            zoho_tool.require_has_more_page({}, "/test", 1, CallerError)

    def test_known_silent_partial_patterns_are_absent(self) -> None:
        needles = (
            '(result.get("page_context") or {}).get("has_more_page")',
            '(response.get("page_context") or {}).get("has_more_page")',
            'page_context = result.get("page_context") or {}',
            'page_context = response.get("page_context") or {}',
        )
        offenders = []
        for path in sorted(ROOT.glob("zoho_*.py")):
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path.name}: {needle}")
        self.assertEqual(
            offenders,
            [],
            "A missing page_context must never be interpreted as the final page",
        )


if __name__ == "__main__":
    unittest.main()
