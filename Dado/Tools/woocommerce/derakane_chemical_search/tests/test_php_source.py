from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = HERE / "plugin" / "frpdepot-derakane-chemical-search"
PLUGIN_PHP = PLUGIN_SOURCE / "frpdepot-derakane-chemical-search.php"
FIXTURE = HERE / "tests" / "fixtures" / "verified"
HARNESS = HERE / "tests" / "php" / "search-harness.php"
PHP = Path(r"C:\Users\TDI-service\AppData\Local\Microsoft\WinGet\Packages\PHP.PHP.8.4_Microsoft.Winget.Source_8wekyb3d8bbwe\php.exe")


@unittest.skipUnless(PHP.is_file(), "PHP CLI is required for real plugin-source tests")
class PhpSearchSourceTests(unittest.TestCase):
    maxDiff = None

    def run_harness(self, action, options=None, plugin_php=PLUGIN_PHP, fixture=FIXTURE):
        result = subprocess.run(
            [str(PHP), str(HARNESS), str(plugin_php), str(fixture), action, json.dumps(options or {}, ensure_ascii=False)],
            cwd=HERE,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def search(self, chemical, concentration="", resin="", offset=0):
        return json.loads(self.run_harness("search", {
            "chemical": chemical, "concentration": concentration, "resin": resin, "offset": offset,
        }))

    def test_exact_alias_cas_starts_with_contains_ranking(self):
        payload = self.search("Alpha")
        self.assertEqual(
            [group["chemical_name"] for group in payload["groups"]],
            ["Alpha", "Other Compound", "Alpha Starter", "Contains Alpha Compound"],
        )

    def test_alias_and_cas_are_searchable(self):
        alias = self.search("Ethanoic Acid")["groups"][0]
        self.assertEqual(alias["chemical_name"], "Acetic Acid")
        self.assertEqual(alias["aliases"][0]["display"], "Ethanoic Acid")
        cas = self.search("64-19-7")["groups"][0]
        self.assertEqual(cas["chemical_name"], "Acetic Acid")
        self.assertEqual(cas["public_cas_numbers"], ["64-19-7"])

    def test_excluded_raw_cas_is_not_publicly_searchable_or_displayed_but_name_search_works(self):
        self.assertEqual(self.search("1330-96-4")["total"], 0)
        by_name = self.search("Malic Acid")
        self.assertEqual(by_name["groups"][0]["chemical_name"], "Malic Acid")
        self.assertEqual(by_name["groups"][0]["public_cas_numbers"], [])
        self.assertNotIn("1330-96-4", json.dumps(by_name, ensure_ascii=False))

    def test_exact_hydrochloric_name_outranks_earlier_contains_match_and_guide_alias_and_cas_work(self):
        payload = self.search("Hydrochloric Acid")
        self.assertEqual(payload["groups"][0]["chemical_name"], "Hydrochloric Acid")
        self.assertEqual(payload["groups"][1]["chemical_name"], "Spent Hydrochloric Acid Wash")
        self.assertEqual(self.search("Muriatic Acid")["groups"][0]["chemical_name"], "Hydrochloric Acid")
        self.assertEqual(self.search("7647-01-0")["groups"][0]["chemical_name"], "Hydrochloric Acid")

    def test_remaining_golden_wrapped_and_alias_records_are_independently_searchable(self):
        cases = {
            "NMP": "N-Methyl-2-Pyrrolidone",
            "Sea Water": "Sea Water",
            "Hydrogen Chloride / Chlorine": "Hydrogen Chloride / Chlorine (condensation or coalescence)",
            "Calcium Chloride": "Calcium Chloride",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(self.search(query)["groups"][0]["chemical_name"], expected)

    def test_known_bad_concentration_orders_remain_source_order(self):
        cases = {
            "Acetone": ["10", "20", "100"],
            "Sulfuric Acid": ["0.5–25", "26–50", "51–70", "71–75", "76–80", ">80"],
            "Sodium Hydroxide": ["<1", "1-2", "3-5", "6-10", "11-25", "26-50"],
        }
        for chemical, expected in cases.items():
            with self.subTest(chemical=chemical):
                rows = self.search(chemical)["groups"][0]["rows"]
                self.assertEqual([row["concentration"]["display"] for row in rows], expected)
                self.assertEqual([row["source_sequence"] for row in rows], sorted(row["source_sequence"] for row in rows))

    def test_malic_and_mixture_concentrations_are_not_absorbed_into_names(self):
        malic = self.search("Malic Acid")["groups"][0]
        self.assertEqual(malic["chemical_name"], "Malic Acid")
        self.assertEqual(malic["rows"][0]["concentration"]["display"], "All")
        mixture = self.search("Ammonium Bifluoride / Sulfuric Acid")["groups"][0]
        self.assertEqual(mixture["chemical_name"], "Ammonium Bifluoride / Sulfuric Acid")
        self.assertEqual(mixture["rows"][0]["concentration"]["display"], "0.1–3:0–75")

    def test_concentration_filter_is_exact_and_never_interpolates(self):
        self.assertEqual(self.search("Acetone", concentration="1")["total"], 0)
        exact = self.search("Acetone", concentration="10")
        self.assertEqual(exact["total"], 1)
        self.assertEqual([row["concentration"]["display"] for row in exact["groups"][0]["rows"]], ["10"])

    def test_resin_filter_selects_only_the_exact_source_cell(self):
        payload = self.search("Acetone", resin="510N")
        self.assertEqual([column["id"] for column in payload["resin_columns"]], ["510N"])
        for row in payload["groups"][0]["rows"]:
            self.assertEqual([cell["resin_id"] for cell in row["cells"]], ["510N"])

    def test_blank_nr_ls_and_source_special_expression_states_remain_distinct(self):
        acetic = self.search("Acetic Acid")["groups"][0]["rows"][0]
        self.assertEqual([acetic["cells"][i]["ratings"][0]["state"] for i in range(3)], ["blank", "nr", "ls"])
        split = self.search("Sodium Hypochlorite", resin="510A/B/C")["groups"][0]["rows"][0]["cells"][0]["ratings"][0]
        self.assertEqual(split["state"], "source_special")
        self.assertEqual(split["source_special_form"], "510_variant_specific_values")
        self.assertEqual(
            [(assignment["label_raw"], assignment["variants"], assignment["rating"]["kind"])
             for assignment in split["resin_variants"]["assignments"]],
            [("510A/B:", ["A", "B"], "temperature_pair"), ("510C:", ["C"], "printed_dash")],
        )
        self.assertEqual(split["resin_variants"]["C"]["state"], "not_printed")

    def test_all_source_special_expression_forms_are_returned_without_flattening(self):
        cases = {
            ("Acetic Acid", "510A/B/C"): ("limited_service_temperature_pair", "temperature_pair"),
            ("Acetone", "470"): ("semicolon_separated_temperature_alternatives", "multiple_alternatives"),
            ("Malic Acid", "411"): ("printed_single_temperature_without_unit", "single_temperature"),
            ("Ammonium Bifluoride / Sulfuric Acid", "515"): ("printed_dash", "printed_dash"),
            ("Sodium Hypochlorite", "510A/B/C"): ("510_variant_specific_values", "resin_variant_values"),
        }
        for (chemical, resin), (form, syntax) in cases.items():
            with self.subTest(form=form):
                rating = self.search(chemical, resin=resin)["groups"][0]["rows"][0]["cells"][0]["ratings"][0]
                self.assertEqual((rating["state"], rating["source_special_form"], rating["syntax"]),
                                 ("source_special", form, syntax))
                self.assertEqual(rating["rating_model_version"], 2)

    def test_all_referenced_row_cell_and_rating_footnotes_are_returned_per_result(self):
        payload = self.search("Acetone")
        self.assertEqual([note["id"] for note in payload["groups"][0]["footnotes"]], [2, 15, 16])

    def test_max_twenty_then_load_more_offset(self):
        first = self.search("Synthetic")
        self.assertEqual(first["total"], 25)
        self.assertEqual(len(first["groups"]), 20)
        self.assertEqual(first["next_offset"], 20)
        second = self.search("Synthetic", offset=20)
        self.assertEqual(len(second["groups"]), 5)
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["groups"][0]["chemical_name"], "Synthetic Reagent 01")
        self.assertEqual(second["groups"][0]["chemical_name"], "Synthetic Reagent 21")

    def test_short_query_and_unknown_resin_fail_closed(self):
        self.assertEqual(self.search("A")["error"], "derakane_short_query")
        self.assertEqual(self.search("Acetone", resin="NOT-A-RESIN")["error"], "derakane_invalid_resin")

    def test_shortcode_has_exact_name_units_legend_notice_and_accessibility(self):
        with tempfile.TemporaryDirectory(prefix="derakane-php-plugin-") as temp:
            plugin = Path(temp) / PLUGIN_SOURCE.name
            shutil.copytree(PLUGIN_SOURCE, plugin)
            shutil.copyfile(FIXTURE / "import-manifest.json", plugin / "data" / "import-manifest.json")
            shutil.copyfile(FIXTURE / "derakane-dataset.json", plugin / "data" / "derakane-dataset.json")
            html = self.run_harness("shortcode", plugin_php=plugin / PLUGIN_PHP.name)
        required = (
            "<h1>Derakane™ Resin Chemical Resistance Guide Search</h1>",
            "Source document SHA-256:", "Verified dataset SHA-256:", "SYNTHETIC TEST EDITION",
            "Important technical limitations", "they are not necessarily maximum service temperatures",
            "<strong>°C/°F</strong>", "<strong>Blank</strong>", "<strong>NR</strong>", "<strong>LS</strong>",
            "No data was available when the guide ratings were assigned.", "Not recommended at any temperature.",
            "Limited service; consult INEOS Technical Service.",
            "Chemical name, synonym, or CAS number", "Concentration (exact guide entry)", "Resin series",
            "aria-live=\"polite\"", "data-derakane-load-more", "No values are interpolated",
        )
        for text in required:
            with self.subTest(text=text):
                self.assertIn(text, html)
        self.assertNotIn("Used with permission", html)

    def test_runtime_rejects_hash_mismatched_dataset(self):
        with tempfile.TemporaryDirectory(prefix="derakane-php-plugin-") as temp:
            plugin = Path(temp) / PLUGIN_SOURCE.name
            shutil.copytree(PLUGIN_SOURCE, plugin)
            shutil.copyfile(FIXTURE / "import-manifest.json", plugin / "data" / "import-manifest.json")
            payload = (FIXTURE / "derakane-dataset.json").read_bytes() + b" "
            (plugin / "data" / "derakane-dataset.json").write_bytes(payload)
            result = self.run_harness("runtime-verified", plugin_php=plugin / PLUGIN_PHP.name)
        self.assertFalse(json.loads(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
