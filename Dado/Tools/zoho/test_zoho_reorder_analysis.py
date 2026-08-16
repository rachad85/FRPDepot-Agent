from __future__ import annotations

import unittest

import zoho_reorder_analysis as tool


class CompletePaginationTests(unittest.TestCase):
    def test_collects_every_page_and_preserves_order(self) -> None:
        calls = []
        payloads = {
            1: {"items": [{"item_id": "1"}], "page_context": {"has_more_page": True}},
            2: {"items": [{"item_id": "2"}], "page_context": {"has_more_page": False}},
        }

        def get(path, params):
            calls.append((path, dict(params)))
            return payloads[params["page"]]

        result = tool.collect_all(get, "/inventory/v1/items", "items")

        self.assertEqual(result, [{"item_id": "1"}, {"item_id": "2"}])
        self.assertEqual(
            calls,
            [
                ("/inventory/v1/items", {"page": 1, "per_page": 200}),
                ("/inventory/v1/items", {"page": 2, "per_page": 200}),
            ],
        )

    def test_accepts_completion_exactly_on_page_ceiling(self) -> None:
        def get(_path, params):
            page = params["page"]
            return {
                "items": [{"item_id": str(page)}],
                "page_context": {"has_more_page": page < 3},
            }

        result = tool.collect_all(get, "/inventory/v1/items", "items", max_pages=3)
        self.assertEqual([row["item_id"] for row in result], ["1", "2", "3"])

    def test_refuses_when_page_ceiling_still_reports_more(self) -> None:
        calls = []

        def get(_path, params):
            calls.append(params["page"])
            return {"items": [], "page_context": {"has_more_page": True}}

        with self.assertRaisesRegex(RuntimeError, "refusing a partial report"):
            tool.collect_all(get, "/inventory/v1/items", "items", max_pages=3)
        self.assertEqual(calls, [1, 2, 3])

    def test_refuses_missing_page_context(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Incomplete pagination metadata"):
            tool.collect_all(
                lambda _path, _params: {"items": []},
                "/inventory/v1/items",
                "items",
            )

    def test_refuses_empty_page_context(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Incomplete pagination metadata"):
            tool.collect_all(
                lambda _path, _params: {"items": [], "page_context": {}},
                "/inventory/v1/items",
                "items",
            )

    def test_refuses_non_boolean_has_more_page(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid has_more_page"):
            tool.collect_all(
                lambda _path, _params: {
                    "items": [],
                    "page_context": {"has_more_page": "false"},
                },
                "/inventory/v1/items",
                "items",
            )

    def test_refuses_non_list_rows(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid items list"):
            tool.collect_all(
                lambda _path, _params: {
                    "items": {"item_id": "1"},
                    "page_context": {"has_more_page": False},
                },
                "/inventory/v1/items",
                "items",
            )

    def test_refuses_non_object_response(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid response"):
            tool.collect_all(
                lambda _path, _params: [],
                "/inventory/v1/items",
                "items",
            )


if __name__ == "__main__":
    unittest.main()
