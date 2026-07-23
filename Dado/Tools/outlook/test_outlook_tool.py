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
from urllib.error import HTTPError

import outlook_tool as tool
import mailbox_audit


def fake_token(scopes: str) -> str:
    header = base64.urlsafe_b64encode(b'{}').decode().rstrip('=')
    payload = base64.urlsafe_b64encode(json.dumps({'scp': scopes}).encode()).decode().rstrip('=')
    return f"{header}.{payload}.signature"


class OutlookToolTests(unittest.TestCase):
    def test_mailbox_audit_excludes_different_company_participants(self) -> None:
        message = {
            "from": {"emailAddress": {"address": "person@troydualam.com"}},
            "toRecipients": [{"emailAddress": {"address": "info@frpdepots.com"}}],
        }
        self.assertTrue(mailbox_audit.has_forbidden_participant(message))
        message["from"]["emailAddress"]["address"] = "customer@example.com"
        self.assertFalse(mailbox_audit.has_forbidden_participant(message))

    def test_device_flow_pending_error_keeps_oauth_error_code(self) -> None:
        response = {
            "error": "authorization_pending",
            "error_description": "Authorization is pending. Continue polling.",
        }
        http_error = HTTPError(
            "https://login.microsoftonline.com/token",
            400,
            "Bad Request",
            {},
            io.BytesIO(json.dumps(response).encode("utf-8")),
        )
        with patch.object(tool, "urlopen", side_effect=http_error):
            with self.assertRaisesRegex(tool.OutlookError, "authorization_pending"):
                tool.http_form("https://login.microsoftonline.com/token", {"x": "y"})

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
                patch.object(tool, "load_official_signature_bundle", return_value=None),
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

    def test_html_draft_uses_official_outlook_signature_and_inline_logo(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            draft_path = folder_path / "draft.json"
            image_path = folder_path / "logo.jpeg"
            image_path.write_bytes(b"not-a-real-logo")
            draft_path.write_text(
                json.dumps(
                    {
                        "to": ["customer@example.com"],
                        "cc": [],
                        "subject": "Pricing",
                        "body_text": "Hello\n\nPricing follows.",
                    }
                ),
                encoding="utf-8",
            )
            bundle = {
                "html": '<p>Rachad Homsi</p><p>CEO</p><img src="cid:logo-cid"><p>www.frpdepots.com</p>',
                "inline_attachments": [
                    {
                        "path": str(image_path),
                        "name": "logo.jpeg",
                        "content_type": "image/jpeg",
                        "content_id": "logo-cid",
                    }
                ],
                "source_message_id": "sent-source-id",
                "source_sent_datetime": "2026-07-21T21:35:35Z",
            }
            calls = []

            def fake_graph(token, method, path, payload=None):
                calls.append((method, path, payload))
                if method == "POST" and path == "/me/messages":
                    return {"id": "draft-html-id", "isDraft": True}
                if method == "POST" and path.endswith("/attachments"):
                    return {"id": "attachment-id"}
                if method == "GET" and path.endswith("/attachments"):
                    return {
                        "value": [
                            {
                                "name": "logo.jpeg",
                                "contentId": "logo-cid",
                                "isInline": True,
                            }
                        ]
                    }
                if method == "GET":
                    return {"id": "draft-html-id", "isDraft": True, "hasAttachments": False}
                self.fail(f"Unexpected Graph call: {method} {path}")

            output = io.StringIO()
            with (
                patch.object(tool, "load_official_signature_bundle", return_value=bundle),
                patch.object(tool, "fit_profile_signature", return_value="Plain fallback"),
                patch.object(tool, "refresh_access_token", return_value=("not-a-real-token", set())),
                patch.object(tool, "graph_request", side_effect=fake_graph),
                patch.object(tool, "append_receipt"),
                redirect_stdout(output),
            ):
                tool.command_draft(argparse.Namespace(input=str(draft_path)))

            create_payload = calls[0][2]
            self.assertEqual(create_payload["body"]["contentType"], "HTML")
            self.assertIn("cid:logo-cid", create_payload["body"]["content"])
            attachment_payload = next(payload for method, path, payload in calls if path.endswith("/attachments"))
            self.assertTrue(attachment_payload["isInline"])
            self.assertEqual(attachment_payload["contentId"], "logo-cid")
            result = json.loads(output.getvalue())
            self.assertEqual(result["inline_signature_images"], 1)
            self.assertEqual(result["official_signature_source_message_id"], "sent-source-id")

    def test_reply_all_preserves_thread_recipients_history_and_replaces_old_draft(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            input_path = folder_path / "reply.json"
            image_path = folder_path / "logo.jpeg"
            image_path.write_bytes(b"not-a-real-logo")
            input_path.write_text(
                json.dumps(
                    {
                        "source_message_id": "source-id",
                        "body_text": "Dear Brian,\n\nPricing follows.",
                        "superseded_draft_id": "old-draft-id",
                        "superseded_subject": "Standalone pricing",
                    }
                ),
                encoding="utf-8",
            )
            bundle = {
                "html": '<p>Rachad Homsi</p><img src="cid:logo-cid"><p>frpdepots.com</p>',
                "inline_attachments": [
                    {
                        "path": str(image_path),
                        "name": "logo.jpeg",
                        "content_type": "image/jpeg",
                        "content_id": "logo-cid",
                    }
                ],
                "source_message_id": "signature-source-id",
            }
            source = {
                "id": "source-id",
                "conversationId": "conversation-id",
                "subject": "RE: Inquiry",
                "from": {"emailAddress": {"address": "brian@example.com"}},
                "toRecipients": [{"emailAddress": {"address": "info@frpdepots.com"}}],
                "ccRecipients": [{"emailAddress": {"address": "buyer@example.com"}}],
                "receivedDateTime": "2026-07-23T10:00:00Z",
                "isDraft": False,
            }
            generated_body = '<div class="quoted">Original history</div>'
            updated_body = ""
            calls = []

            def fake_graph(token, method, path, payload=None):
                nonlocal updated_body
                calls.append((method, path, payload))
                if method == "GET" and path.startswith("/me/messages/source-id?"):
                    return source
                if method == "GET" and path.startswith("/me/messages?"):
                    return {"value": [source]}
                if method == "GET" and path.startswith("/me/messages/old-draft-id?"):
                    return {"id": "old-draft-id", "isDraft": True, "subject": "Standalone pricing"}
                if method == "POST" and path.endswith("/createReplyAll"):
                    return {"id": "reply-id", "isDraft": True}
                if method == "GET" and path.startswith("/me/messages/reply-id?"):
                    body = updated_body or generated_body
                    return {
                        "id": "reply-id",
                        "isDraft": True,
                        "conversationId": "conversation-id",
                        "subject": "RE: Inquiry",
                        "body": {"contentType": "HTML", "content": body},
                        "toRecipients": [{"emailAddress": {"address": "brian@example.com"}}],
                        "ccRecipients": [{"emailAddress": {"address": "buyer@example.com"}}],
                        "bccRecipients": [],
                    }
                if method == "PATCH" and path == "/me/messages/reply-id":
                    updated_body = payload["body"]["content"]
                    return {"id": "reply-id", "isDraft": True}
                if method == "POST" and path.endswith("/attachments"):
                    return {"id": "attachment-id"}
                if method == "GET" and path.endswith("/attachments"):
                    return {"value": [{"contentId": "logo-cid", "isInline": True}]}
                if method == "DELETE" and path == "/me/messages/old-draft-id":
                    return {}
                self.fail(f"Unexpected Graph call: {method} {path}")

            output = io.StringIO()
            with (
                patch.object(tool, "load_official_signature_bundle", return_value=bundle),
                patch.object(tool, "refresh_access_token", return_value=("not-a-real-token", set())),
                patch.object(tool, "graph_request", side_effect=fake_graph),
                patch.object(tool, "append_receipt"),
                redirect_stdout(output),
            ):
                tool.command_reply_all(argparse.Namespace(input=str(input_path)))

            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "REPLY_ALL_DRAFT_CREATED_NOT_SENT")
            self.assertEqual(result["conversation_id"], "conversation-id")
            self.assertEqual(result["to"], ["brian@example.com"])
            self.assertEqual(result["cc"], ["buyer@example.com"])
            self.assertTrue(result["quoted_history_preserved"])
            self.assertTrue(result["superseded_draft_removed"])
            self.assertEqual(updated_body.count('class="frp-depots-official-signature"'), 1)
            self.assertIn(generated_body, updated_body)
            self.assertTrue(any(method == "DELETE" for method, _, _ in calls))

    def test_resolve_source_message_picks_newest_external_and_rejects_ambiguous(self) -> None:
        recent = {
            "value": [
                {"id": "own", "conversationId": "c0", "subject": "internal note",
                 "from": {"emailAddress": {"address": "info@frpdepots.com"}},
                 "receivedDateTime": "2026-07-23T12:00:00Z", "isDraft": False},
                {"id": "brian-new", "conversationId": "c1", "subject": "RE: Pricing",
                 "from": {"emailAddress": {"address": "brian@example.com"}},
                 "receivedDateTime": "2026-07-23T11:00:00Z", "isDraft": False},
                {"id": "brian-old", "conversationId": "c1", "subject": "Pricing",
                 "from": {"emailAddress": {"address": "brian@example.com"}},
                 "receivedDateTime": "2026-07-22T09:00:00Z", "isDraft": False},
                {"id": "brian-draft", "conversationId": "c1", "subject": "Pricing",
                 "from": {"emailAddress": {"address": "brian@example.com"}},
                 "receivedDateTime": "2026-07-23T11:30:00Z", "isDraft": True},
            ]
        }
        with patch.object(tool, "graph_request", return_value=recent):
            # newest EXTERNAL, non-draft message wins; the draft is ignored
            self.assertEqual(tool.resolve_source_message("t", "brian@example.com")["id"], "brian-new")
            # a term that only hits own-domain mail is not a valid external target
            with self.assertRaises(tool.OutlookError):
                tool.resolve_source_message("t", "internal note")
        ambiguous = {
            "value": [
                {"id": "a", "conversationId": "c1", "subject": "Pricing",
                 "from": {"emailAddress": {"address": "a@x.com"}},
                 "receivedDateTime": "2026-07-23T11:00:00Z", "isDraft": False},
                {"id": "b", "conversationId": "c2", "subject": "Pricing",
                 "from": {"emailAddress": {"address": "b@y.com"}},
                 "receivedDateTime": "2026-07-23T10:00:00Z", "isDraft": False},
            ]
        }
        with patch.object(tool, "graph_request", return_value=ambiguous):
            with self.assertRaises(tool.OutlookError):
                tool.resolve_source_message("t", "Pricing")

    def test_draft_is_blocked_without_signature(self) -> None:
        with (
            patch.object(tool, "load_official_signature_bundle", return_value=None),
            patch.object(tool, "fit_profile_signature", return_value=""),
        ):
            with self.assertRaisesRegex(tool.OutlookError, "signature is missing"):
                tool.command_draft(argparse.Namespace(input="unused.json"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
