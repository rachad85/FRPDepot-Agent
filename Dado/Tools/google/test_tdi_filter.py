"""Tests for the TDI screen.

Two failure modes matter and they pull in opposite directions:
  UNDER-blocking lets Troy Dualam material into an FRP Depot answer (Hard Rule 4).
  OVER-blocking walls off Rachad's own mail, which he has explicitly forbidden
  ("never add a wall I did not ask for"). Both are tested here.
"""
from __future__ import annotations

import unittest

from tdi_filter import deep_tdi_marker, is_tdi_flagged


class MustCatch(unittest.TestCase):
    """Real TDI material observed in the live index."""

    def test_company_name_and_domain(self) -> None:
        self.assertTrue(is_tdi_flagged("quote from Troy Dualam"))
        self.assertTrue(is_tdi_flagged("anh@troydualam.com"))

    def test_observed_misspelling(self) -> None:
        self.assertEqual(deep_tdi_marker("Troy Dumalac INC show CRA taxes owed"),
                         "dumalac(misspelling)")

    def test_quote_number_after_underscores(self) -> None:
        """\\b never fires between '_' and 'Q' — this filename really leaked."""
        name = "RE_ RFQ for dual laminate dome and bottoms______________Q26-1526.msg"
        self.assertEqual(deep_tdi_marker(name, name=name), "Q26 quote number")

    def test_plain_quote_number(self) -> None:
        self.assertEqual(deep_tdi_marker("Q26-1483 PPE Tanks"), "Q26 quote number")

    def test_agent_artifacts(self) -> None:
        self.assertEqual(deep_tdi_marker("aze_active_task.json"), "aze artifact")
        self.assertEqual(deep_tdi_marker("x_aze_receipts.jsonl"), "aze artifact")

    def test_artifact_by_filename_without_extension(self) -> None:
        self.assertEqual(deep_tdi_marker("", name="aze_runtime"), "aze artifact filename")

    def test_tdi_tree_and_database(self) -> None:
        self.assertEqual(deep_tdi_marker("C:\\AgentTeam\\Aze"), "AgentTeam(TDI tree)")
        self.assertEqual(deep_tdi_marker("lineage of troy_history"), "troy_history(TDI db)")

    def test_tdi_token_with_various_neighbours(self) -> None:
        for text in ("sent to TDI", "Report for TDI-1234", "file_TDI_summary", "(TDI)"):
            with self.subTest(text=text):
                self.assertEqual(deep_tdi_marker(text), "tdi")


class MustNotCatch(unittest.TestCase):
    """Rachad's own unrelated data. Blocking any of this is a bug."""

    def test_parcel_deliveries_to_a_person_named_troy(self) -> None:
        """368 of the corpus's 'troy' hits look like this; only 5 are the company."""
        self.assertEqual(deep_tdi_marker("Arriving tomorrow Troy - Elizabethtown, Ontario"), "")
        self.assertEqual(deep_tdi_marker("Dear Troy, Thank you for your recent order"), "")

    def test_turkish_text_is_not_tdi(self) -> None:
        """'yurtdisi' lowercases into a run containing t-d-i under Unicode casing."""
        turkish = "Yurtd\u0131\u015f\u0131 seferlerimizle seyahat ederek"
        self.assertFalse(is_tdi_flagged(turkish))
        self.assertEqual(deep_tdi_marker(turkish), "")

    def test_ad_tracking_token_is_not_an_aze_artifact(self) -> None:
        token = "https://x.example/?e=6h2iLsV-AZE_q0BIWA&n=1&u=aHR0cHM6"
        self.assertEqual(deep_tdi_marker(token, name=token), "")

    def test_bare_q26_without_a_number(self) -> None:
        self.assertEqual(deep_tdi_marker("quarter Q26 review"), "")

    def test_ordinary_frp_business(self) -> None:
        for text in ("FRP pipe elbow 150PSI quote",
                     "New invoice INV00102",
                     "amazed by the blazer in the gazette"):
            with self.subTest(text=text):
                self.assertEqual(deep_tdi_marker(text), "")


if __name__ == "__main__":
    unittest.main()
