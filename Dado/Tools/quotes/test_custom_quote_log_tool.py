from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import custom_quote_log_tool as tool


class CustomQuoteLogTests(unittest.TestCase):
    def sample(self) -> dict:
        return {
            "quote_date": "2026-07-27",
            "customer_company": "KENZ Jordan",
            "contact_name": "Lina Qandeel",
            "contact_email": "kz.16sales@kenzjordan.com",
            "customer_reference": "2600AM-KE4288",
            "subject": "RE: KENZ Ref. 2600AM-KE4288",
            "currency": "usd",
            "total": "184470",
            "attachment_name": "quote.pdf",
            "sent_message_id": "sent-message-1",
            "source_pricing": "Rachad workbook; approved markup",
            "notes": "Shipping excluded",
        }

    def test_record_is_single_use_and_assigns_internal_id(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            log_path = Path(folder) / "custom_quotes_log.csv"
            verified = {
                "sent_at_utc": "2026-07-27T16:05:23Z",
                "conversation_id": "conversation-1",
                "attachment_sha256": "a" * 64,
                "to": ["kz.16sales@kenzjordan.com"],
                "cc": [],
                "mail_send_scope_present": False,
            }
            with (
                patch.object(tool, "LOG_PATH", log_path),
                patch.object(tool, "verify_sent_quote", return_value=verified),
                patch.object(tool, "append_receipt"),
            ):
                result = tool.record_quote(self.sample())
                with self.assertRaisesRegex(tool.QuoteLogError, "already logged"):
                    tool.record_quote(self.sample())

            self.assertEqual(result["internal_id"], "CQ-2026-0001")
            self.assertEqual(result["total"], "184470.00")
            with log_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["outside_zoho"], "yes")
            self.assertEqual(rows[0]["status"], "sent")

    def test_invalid_currency_is_rejected(self) -> None:
        data = self.sample()
        data["currency"] = "US dollars"
        with self.assertRaisesRegex(tool.QuoteLogError, "three-letter"):
            tool.validate_input(data)


if __name__ == "__main__":
    unittest.main()
