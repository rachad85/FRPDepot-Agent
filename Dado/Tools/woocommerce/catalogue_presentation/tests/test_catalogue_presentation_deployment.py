from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[2] / "catalogue_presentation_deployment_tool.py"
SPEC = importlib.util.spec_from_file_location("catalogue_presentation_deployment_tool", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
DEPLOY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOY)


class CataloguePresentationDeploymentTests(unittest.TestCase):
    def projection(self, categories, products, grouping):
        return {
            "present": True,
            "categories": categories,
            "products": products,
            "grouping": grouping,
            "order_bytes": DEPLOY.canonical({"categories": categories, "products": products,
                                               "grouping": grouping}).encode("utf-8").hex(),
            "deduplicated": len(categories) == len(set(categories))
            and len(products) == len(set(products)),
            "fully_grouped": [product for _, group in grouping for product in group] == products,
            "nonempty_groups": all(group for _, group in grouping),
        }

    def test_hardcoded_artifact_and_member_hashes_verify(self):
        record = DEPLOY.verify_artifact()
        self.assertEqual(record["version"], "1.2.1")
        self.assertEqual(record["sha256"], DEPLOY.ARTIFACT_SHA256)
        self.assertEqual(record["bytes"], DEPLOY.ARTIFACT_BYTES)
        self.assertEqual(record["members"], sorted(DEPLOY.ARTIFACT_MEMBER_SHA256))

    def test_member_hash_mismatch_fails_closed(self):
        wrong = dict(DEPLOY.ARTIFACT_MEMBER_SHA256)
        wrong[next(iter(wrong))] = "0" * 64
        with mock.patch.object(DEPLOY, "ARTIFACT_MEMBER_SHA256", wrong), \
                self.assertRaises(DEPLOY.DeploymentError):
            DEPLOY.verify_artifact()

    def test_live_predecessor_artifact_is_explicitly_superseded(self):
        self.assertIn(
            "740b350d2df8b7513da956161fdd1346ae99fae65a9a38cb0d4db3aae7c04346",
            DEPLOY.SUPERSEDED_ARTIFACT_SHA256,
        )

    def test_responsive_table_validator_requires_real_mobile_scroll(self):
        page = mock.Mock()
        scroller = mock.Mock()
        scroller.query_selector_all.return_value = [mock.Mock()]
        scroller.evaluate.return_value = {
            "client_width": 320,
            "scroll_width": 960,
            "overflow_x": "auto",
            "cue_display": "block",
            "cue_id": "frpdepot-acp-specification-table-instructions",
            "tabindex": "0",
            "aria_label": "Scrollable product specifications",
            "aria_describedby": "frpdepot-acp-specification-table-instructions",
            "scroll_left_before": 0,
            "scroll_left_after": 640,
            "document_client_width": 390,
            "document_scroll_width": 390,
        }
        selectors = {
            ".frpdepot-acp-specification-table-cue": [mock.Mock()],
            ".et_pb_wc_description .spec-table-wrapper.frpdepot-acp-responsive-active": [scroller],
            "#frpdepot-acp-responsive-table-styles": [mock.Mock()],
            "#frpdepot-acp-responsive-table-script": [mock.Mock()],
        }
        page.query_selector_all.side_effect = lambda selector: selectors[selector]
        reader = DEPLOY.PublicPage(page, mock.Mock())
        with mock.patch.object(reader, "require_healthy", return_value={"blank": False, "fatal": False}):
            row = reader._responsive_table_findings(1368)
        self.assertTrue(row["passed"])
        self.assertTrue(row["target"])
        self.assertEqual(page.set_viewport_size.call_args_list[0].args[0],
                         DEPLOY.MOBILE_TABLE_VIEWPORT)

    def test_responsive_table_validator_rejects_clipped_target(self):
        page = mock.Mock()
        scroller = mock.Mock()
        scroller.query_selector_all.return_value = [mock.Mock()]
        scroller.evaluate.return_value = {
            "client_width": 320,
            "scroll_width": 320,
            "overflow_x": "hidden",
            "cue_display": "none",
            "cue_id": None,
            "tabindex": None,
            "aria_label": None,
            "aria_describedby": None,
            "scroll_left_before": 0,
            "scroll_left_after": 0,
            "document_client_width": 390,
            "document_scroll_width": 960,
        }
        page.query_selector_all.side_effect = lambda selector: {
            ".frpdepot-acp-specification-table-cue": [mock.Mock()],
            ".et_pb_wc_description .spec-table-wrapper.frpdepot-acp-responsive-active": [scroller],
            "#frpdepot-acp-responsive-table-styles": [mock.Mock()],
            "#frpdepot-acp-responsive-table-script": [mock.Mock()],
        }[selector]
        reader = DEPLOY.PublicPage(page, mock.Mock())
        with mock.patch.object(reader, "require_healthy", return_value={"blank": False, "fatal": False}):
            row = reader._responsive_table_findings(1397)
        self.assertFalse(row["passed"])

    def test_identical_grouped_projection_with_future_descendant_is_allowed(self):
        required_categories = sorted(DEPLOY.EXPECTED_CATEGORY_IDS)
        required_products = sorted(DEPLOY.EXPECTED_PRODUCT_IDS)
        categories = required_categories + [61]
        products = required_products + [9001]
        grouping = [[category, [product]] for category, product
                    in zip(categories, products[:len(categories)])]
        grouping[-1][1].extend(products[len(categories):])
        projection = self.projection(categories, products, grouping)
        self.assertTrue(DEPLOY.PublicPage._matches_catalogue_projection(
            projection, projection))

    def test_surface_grouping_or_order_mismatch_fails_closed(self):
        categories = sorted(DEPLOY.EXPECTED_CATEGORY_IDS)
        products = sorted(DEPLOY.EXPECTED_PRODUCT_IDS)
        reference = self.projection(categories, products, [[categories[0], products]])
        changed = self.projection(categories, products, [[categories[-1], products]])
        self.assertFalse(DEPLOY.PublicPage._matches_catalogue_projection(
            changed, reference))

    def test_empty_future_descendant_group_fails_closed(self):
        categories = sorted(DEPLOY.EXPECTED_CATEGORY_IDS) + [61]
        products = sorted(DEPLOY.EXPECTED_PRODUCT_IDS)
        grouping = [[category, [product]] for category, product
                    in zip(categories[:-1], products[:-1])]
        grouping[-1][1].append(products[-1])
        grouping.append([61, []])
        projection = self.projection(categories, products, grouping)
        self.assertFalse(DEPLOY.PublicPage._matches_catalogue_projection(
            projection, projection))

    def test_missing_required_entry_or_duplicate_fails_closed(self):
        categories = sorted(DEPLOY.EXPECTED_CATEGORY_IDS)
        products = sorted(DEPLOY.EXPECTED_PRODUCT_IDS)
        missing = self.projection(categories, products[:-1], [[categories[0], products[:-1]]])
        self.assertFalse(DEPLOY.PublicPage._matches_catalogue_projection(missing, missing))
        duplicate = self.projection(categories, products + [products[-1]],
                                    [[categories[0], products + [products[-1]]]])
        self.assertFalse(DEPLOY.PublicPage._matches_catalogue_projection(
            duplicate, duplicate))


if __name__ == "__main__":
    unittest.main()
