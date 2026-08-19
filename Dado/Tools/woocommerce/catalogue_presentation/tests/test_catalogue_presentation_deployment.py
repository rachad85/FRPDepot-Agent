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
        self.assertEqual(record["version"], "1.2.0")
        self.assertEqual(record["sha256"], DEPLOY.ARTIFACT_SHA256)
        self.assertEqual(record["bytes"], DEPLOY.ARTIFACT_BYTES)
        self.assertEqual(record["members"], sorted(DEPLOY.ARTIFACT_MEMBER_SHA256))

    def test_member_hash_mismatch_fails_closed(self):
        wrong = dict(DEPLOY.ARTIFACT_MEMBER_SHA256)
        wrong[next(iter(wrong))] = "0" * 64
        with mock.patch.object(DEPLOY, "ARTIFACT_MEMBER_SHA256", wrong), \
                self.assertRaises(DEPLOY.DeploymentError):
            DEPLOY.verify_artifact()

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
