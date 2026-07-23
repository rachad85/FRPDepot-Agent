from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import outlook_tool as tool


def fake_token(scopes: str) -> str:
    header = base64.urlsafe_b64encode(b'{}').decode().rstrip('=')
    payload = base64.urlsafe_b64encode(json.dumps({'scp': scopes}).encode()).decode().rstrip('=')
    return f"{header}.{payload}.signature"


class OutlookToolTests(unittest.TestCase):
    def test_scope_boundary_excludes_send(self) -> None:
        self.assertNotIn("https://graph.microsoft.com/Mail.Send", tool.REQUESTED_SCOPES)
        self.assertEqual(tool.FORBIDDEN_TOKEN_SCOPE, "Mail.Send")

    def test_required_token_scopes_are_accepted(self) -> None:
        scopes = tool.decode_token_scopes(
            fake_token("User.Read Mail.ReadWrite Calendars.Read offline_access")
        )
        self.assertIn("Mail.ReadWrite", scopes)

    def test_send_scope_is_rejected(self) -> None:
        with self.assertRaisesRegex(tool.OutlookError, "drafts-only"):
            tool.decode_token_scopes(
                fake_token("User.Read Mail.ReadWrite Calendars.Read Mail.Send")
            )

    def test_dpapi_round_trip(self) -> None:
        plaintext = b"unit-test-refresh-token-not-real"
        protected = tool.dpapi_protect(plaintext)
        self.assertNotEqual(protected, plaintext)
        self.assertEqual(tool.dpapi_unprotect(protected), plaintext)

    def test_signature_is_read_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fit.md"
            path.write_text(
                "# Test\n- Rachad's standard email signature block (paste exactly):\n"
                "  Rachad Homsi\n  FRP Depot\n  555-0100\n\n## Next\n",
                encoding="utf-8",
            )
            self.assertEqual(
                tool.fit_profile_signature(path),
                "Rachad Homsi\nFRP Depot\n555-0100",
            )

    def test_draft_is_created_only_as_draft_and_appends_signature(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            draft_path = Path(folder) / "draft.json"
            draft_path.write_text(
                json.dumps(
                    {
                        "to": ["customer@example.com"],
                        "cc": [],
                        "subject": "Test subject",
                        "body_text": "Test body",
                    }
                ),
                encoding="utf-8",
            )
            captured_payload = {}

            def fake_graph(token, method, path, payload=None):
                self.assertEqual(method, "POST")
                self.assertEqual(path, "/me/messages")
                captured_payload.update(payload)
                return {"id": "draft-id-123", "isDraft": True}

            output = io.StringIO()
            with (
                patch.object(tool, "fit_profile_signature", return_value="Rachad Signature"),
                patch.object(tool, "refresh_access_token", return_value=("not-a-real-token", set())),
                patch.object(tool, "graph_request", side_effect=fake_graph),
                patch.object(tool, "append_receipt"),
                redirect_stdout(output),
            ):
                tool.command_draft(argparse.Namespace(input=str(draft_path)))

            self.assertEqual(
                captured_payload["body"]["content"],
                "Test body\n\nRachad Signature",
            )
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "DRAFT_CREATED_NOT_SENT")
            self.assertEqual(result["id"], "draft-id-123")

    def test_draft_is_blocked_without_signature(self) -> None:
        with patch.object(tool, "fit_profile_signature", return_value=""):
            with self.assertRaisesRegex(tool.OutlookError, "signature is missing"):
                tool.command_draft(argparse.Namespace(input="unused.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
