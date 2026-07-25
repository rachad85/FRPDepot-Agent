"""Guards on the OAuth sign-in flow for both Google auth modules.

These are AST assertions rather than mocked sign-ins on purpose: the failure
being guarded is a MISSING keyword argument, which no unit test that stubs the
flow object would ever notice - a stub happily accepts whatever it is given.
Reading the call as written in the source is the only check that catches it.

WHY (2026-07-24): google_auth.py called run_local_server() with no `prompt`.
google_auth_oauthlib defaults access_type to "offline" but sets no prompt, and
Google only returns a refresh token on the FIRST authorization of a client+user
grant or when prompt=consent forces re-approval. Since Rachad had already
granted these scopes to dado-frpd, `reconnect` re-ran an already-granted
request, Google auto-approved, and the response carried refresh_token=null -
which _save() wrote over the working token. Sign-in printed success; an hour
later every non-interactive caller lost access at once.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
MODULES = ("google_auth.py", "google_extended_auth.py")


def run_local_server_keywords(source_path: Path) -> dict[str, object]:
    """Keyword arguments of the run_local_server(...) call, as written."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "run_local_server":
            found = {}
            for keyword in node.keywords:
                if keyword.arg is None:
                    continue
                try:
                    found[keyword.arg] = ast.literal_eval(keyword.value)
                except ValueError:
                    found[keyword.arg] = "<non-literal>"
            return found
    raise AssertionError(f"No run_local_server(...) call found in {source_path.name}")


class GoogleAuthFlowTests(unittest.TestCase):
    def test_both_modules_force_offline_access_and_a_consent_prompt(self) -> None:
        for module in MODULES:
            with self.subTest(module=module):
                kwargs = run_local_server_keywords(HERE / module)
                self.assertEqual(
                    kwargs.get("access_type"), "offline",
                    f"{module}: access_type must be 'offline' or Google may issue no "
                    "refresh token.",
                )
                self.assertEqual(
                    kwargs.get("prompt"), "consent",
                    f"{module}: prompt must be 'consent'. Without it Google auto-approves "
                    "an already-granted scope set and returns refresh_token=null, which "
                    "overwrites the working token and kills every non-interactive caller.",
                )

    def test_no_send_scope_anywhere_in_either_module(self) -> None:
        """Golden Rule 1: drafts only. There is no send path in this tree."""
        for module in MODULES:
            with self.subTest(module=module):
                text = (HERE / module).read_text(encoding="utf-8")
                self.assertNotIn("gmail.send", text)
                self.assertNotIn("mail.google.com/", text)


if __name__ == "__main__":
    unittest.main()
