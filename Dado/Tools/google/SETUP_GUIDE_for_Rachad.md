# Google (personal Gmail read-only + Drive read-only + Gmail drafts) — your one-time setup (~10 minutes)

You do this once, like you did for Aze's Google connection. Dado gets its
OWN sign-in on its OWN app registration — separate from Aze's "TDI Aze Read
Only" project, so revoking one never touches the other. Same personal
account (rachad85@gmail.com), different app. Follow the steps in order;
there is a CHECK after each. Stop at any CHECK that fails and tell Claude
what you saw.

THE ONE THING TO KNOW FIRST (worst news up front): Google expires this
sign-in every 7 days for personal accounts — their rule, no way around it
without a paid Google audit. Renewing is ONE double-click of
CONNECT_DADO_GOOGLE.bat. The tools will tell you plainly when it is due.

--------------------------------------------------------------------
## STEP 1 — Create a free Google Cloud project

1. Go to  https://console.cloud.google.com  and sign in with
   rachad85@gmail.com. If it asks you to agree to terms, tick and continue.
   CHECK: the page header says "Google Cloud".

2. At the TOP-LEFT, click the project picker (says "Select a project"
   or a project name) → "NEW PROJECT".
   Name it:  FRP Depot Dado Read Only
   Click "CREATE", wait a few seconds, then click "SELECT PROJECT"
   in the notification (bell icon) if it doesn't switch by itself.
   CHECK: the top bar now shows "FRP Depot Dado Read Only".

--------------------------------------------------------------------
## STEP 2 — Turn on the APIs

3. Left menu (☰) → "APIs & Services" → "Library".
   Search:  Gmail API  → click it → click "ENABLE".
   CHECK: the button changes to "MANAGE".

4. Click "← " back to the Library (or search again from the top bar).
   Search:  Google Drive API  → click it → click "ENABLE".
   CHECK: the button changes to "MANAGE".

(No People API this time — Dado isn't getting Contacts access.)

--------------------------------------------------------------------
## STEP 3 — Tell Google who may use this app (only you)

5. Left menu → "APIs & Services" → "OAuth consent screen".
   (On newer screens this is called "Google Auth Platform" — same thing.)
   If it shows a "GET STARTED" button, click it and fill in:
      App name:       FRP Depot Dado
      Support email:  rachad85@gmail.com
      Audience:       External
      Contact email:  rachad85@gmail.com
   Agree and click "CREATE" / "FINISH".
   CHECK: you land on the app's overview page.

6. Find "Audience" in the left menu of that page. Scroll to
   "Test users" → click "+ ADD USERS" → type  rachad85@gmail.com  → SAVE.
   CHECK: your email is listed under Test users.
   (This is what lets YOU sign in while the app stays private. The
   "Publishing status" stays "Testing" — do NOT publish.)

--------------------------------------------------------------------
## STEP 4 — Create the key file and download it

7. Left menu → "APIs & Services" → "Credentials" →
   "+ CREATE CREDENTIALS" (top) → "OAuth client ID".
      Application type:  Desktop app        ← must be exactly this
      Name:              FRP Depot Dado Desktop
   Click "CREATE".
   CHECK: a box pops up saying "OAuth client created".

8. In that box click "DOWNLOAD JSON". Let it go to your normal Downloads
   folder. The connect button finds it there by itself, renames it, and
   puts it here:
        C:\FRPDepot\Dado\Tools\google\google_client.json
   (If you'd rather place it by hand: move the downloaded file into that
   folder and rename it to exactly google_client.json.)
   CHECK: your Downloads folder has a file starting with "client_secret"
   (or that folder now has google_client.json).
   Never paste anything from this file into chat. It is git-ignored, so it
   stays on this PC and never syncs to GitHub.

--------------------------------------------------------------------
## STEP 5 — Sign in once

9. Double-click  CONNECT_DADO_GOOGLE.bat  at the repo root
   (C:\FRPDepot\CONNECT_DADO_GOOGLE.bat).
   A browser opens:
      - pick rachad85@gmail.com
      - Google shows "Google hasn't verified this app" — that is YOUR
        own private app it hasn't reviewed. Click "Continue".
      - TICK EVERY BOX, then "Continue".
   CHECK: the black window prints
      GMAIL OK — signed in as rachad85@gmail.com … Drafting: ON …
      DRIVE OK — signed in as rachad85@gmail.com … Read-only, by design.
      SIGNED IN OK.

   The checkbox wording: Google will show something like "Read, compose,
   and send email" for the Gmail box — that's Google's bundled wording, not
   what Dado does. There is no send call anywhere in google_tool.py; drafts
   only, verified by reading each one back after creating it. The Drive box
   should read as view/download only — no edit or delete wording, because
   this token never requests that scope. Tick the boxes.

10. Tell Claude "Google is connected" so the backend runs the live
    verification and switches Dado on for it.

--------------------------------------------------------------------
## Weekly renewal (normal, not a fault)

When Dado or a tool says the Google sign-in EXPIRED: double-click
CONNECT_DADO_GOOGLE.bat, pick your account, Continue, done. ~30 seconds.

--------------------------------------------------------------------
## TROUBLESHOOTING — "Error 403: access_denied" (hit 2026-07-24)

Full wording: "Access blocked: Dado_FRPD has not completed the Google
verification process ... can only be accessed by developer-approved
testers."

WHAT IT MEANS: nothing is broken and nothing is wrong with the code. Your
app is in "Testing" publishing status (correct — keep it there), and in
that mode Google only lets accounts on the Test users list sign in. Your
account is not on that list yet. This is STEP 3 item 6 above.

FIX (project: dado-frpd):
1. https://console.cloud.google.com — confirm the project picker at the
   top says the project holding this app (dado-frpd).
2. Left menu -> "APIs & Services" -> "OAuth consent screen"
   (newer console calls it "Google Auth Platform").
3. Click "Audience" in the left menu of that page.
4. Scroll to "Test users" -> "+ ADD USERS" -> type rachad85@gmail.com
   -> ADD/SAVE.
   CHECK: rachad85@gmail.com is now listed under Test users.
5. Leave "Publishing status" as Testing. Do NOT click "PUBLISH APP" —
   publishing sends the app for Google review, which is slow and
   unnecessary for a private one-user app.
6. Close the failed browser tab, then double-click
   CONNECT_DADO_GOOGLE.bat again.

IF IT STILL FAILS: the usual cause is that the OAuth client was created
in a DIFFERENT project than the one where you added the test user. The
client file in use belongs to project "dado-frpd" — make sure that is the
project you edited in step 1.
