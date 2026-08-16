---
name: frp-web-release-qa
description: Use for every FRP website release and visual QA.
---

# FRP Web Release QA

Use this skill for every FRP Depot WordPress/WooCommerce/plugin release, public-site defect, responsive visual audit, or post-deployment verification.

QA is read-only. This skill does **not** authorize a website change or generic browser automation. Any deployment must still use its commissioned named stage/commit tool, immutable plan, browser mutex, and Rachad's fresh exact `APPROVED`.

## Permanent catalogue QA tool

For the automatic catalogue-presentation release, use:

`C:\FRPDepot\Dado\Tools\woocommerce\catalogue_presentation_release_qa_tool.py`

It exposes only two commands:

1. `audit` — fixed anonymous public reads, fixed 1440×1000 and 390×844 browser checks, 42 screenshots, protected catalogue regressions, and a fresh read of the exact plugin row in a new authenticated WordPress tab under the shared browser mutex. It performs no website click except opening the fixed public mobile menu and has no business-write primitive.
2. `finalize --report <automated_report.json> --review <pixel_review.json>` — validates the report digest, every screenshot path/hash, complete one-row-per-screenshot review, and review/result consistency, then writes one immutable final result. It refuses an automated failure, missing/changed screenshot, incomplete review, contradictory result, escaped path, or existing final result.

The `audit` command normally exceeds two minutes, so launch it through `Dado\Tools\watch\job_runner.py start --name catalogue-release-qa -- <full command>` and end the turn. Never poll-loop it. The job's exit `0` means only `AUTOMATED_GATES_PASSED_PIXEL_REVIEW_REQUIRED`; do not call the release passed until `finalize` returns `PASSED`.

The first live tool run on 2026-08-15 proved why no-cache scoping matters: context-wide `Cache-Control` propagated to Google Fonts and Stripe, manufactured CORS preflights, and correctly failed 28 pages despite the site itself being healthy. The corrected tool applies no-cache headers only to same-origin FRP Depot document navigations and uses cache-busting queries; cross-origin subresources receive no injected header.

## 1. Define the release contract first

Before staging a deployment, write down:

- exact public routes and authenticated/protected routes affected;
- desktop and phone viewport sizes;
- intended visible change;
- protected navigation, content, products, categories, downloads and privacy boundaries;
- forbidden text, files and destinations;
- expected plugin/version/state;
- rollback or emergency-deactivation behavior already commissioned;
- baseline screenshots and machine-readable findings.

Do not infer a release passed because its source tests passed. Source, artifact, installed state, public DOM, browser behavior and pixels are separate evidence layers.

## 2. Pre-deployment checks

1. Verify source and built artifact identity, member set, byte size and SHA-256.
2. Compile/lint the changed code.
3. Run focused tests plus the complete relevant deployment suite.
4. Test the actual stage-versus-commit predicates using `frp-commissioned-write-lifecycle`.
5. Baseline every affected route before the write.
6. Confirm the deployment tool can change only the fixed plugin/action and cannot delete, send, alter settings/content/users, or perform generic website operations.
7. Stage only through the named tool. Each deactivate, replace and activate action needs its own immutable plan and fresh approval.

## 3. Mandatory post-deployment evidence layers

A release is not passed until all layers below are complete.

### A. Installed-state proof

Fresh-read the commissioned WordPress plugin row and prove:

- exact plugin file;
- exact version;
- exact active/inactive state;
- no unexpected update marker;
- immutable plan result and replay lock.

### B. Uncached HTML and response scan

Fetch each affected public route with a cache-busting query and `Cache-Control: no-cache` on the **same-origin document navigation only**. Never set that header context-wide: it propagates to cross-origin fonts/payment frames and can manufacture CORS errors absent from a normal visit. Record status, final URL and response byte count.

Search visible text and HTML case-insensitively for at least:

- `PHP Warning`, `PHP Notice`, `Deprecated`, `Fatal error`, `Parse error`;
- `Undefined property`, `Undefined variable`, `Undefined index`, `Undefined array key`;
- `Uncaught Error`, `Uncaught Exception`, `Stack trace`;
- `/home/.../public_html`, `wp-content/...php on line`, and other server filesystem paths.

Do not use only a short fatal-error phrase list. A page can return HTTP 200 while visibly leaking a warning and server path.

### C. Browser/DOM checks at every viewport

At minimum use desktop `1440×1000` and phone `390×844`, unless the release contract names additional sizes.

For every route record:

- HTTP status and final URL;
- title and visible H1s;
- document width, viewport width and horizontal overflow;
- blank or abnormally short body;
- broken images;
- visible empty headings;
- invalid or placeholder links;
- console errors;
- failed network responses;
- required and forbidden content counts;
- navigation destinations and deduplication;
- menus or other interaction states after they are opened.

Treat horizontal overflow above 2px as a failure unless a documented component intentionally scrolls and is proven contained.

### D. Screenshot and pixel review

Capture full-page screenshots for every route and viewport plus relevant interaction states, such as an opened mobile menu.

**Someone must inspect the pixels.** Saving screenshots is not review. Automated layout metrics do not detect visible PHP text, path disclosure, ugly wraps, overlap, clipping, blank sections, or the wrong image.

For efficient review:

1. Keep the original full-resolution screenshots.
2. Build contact sheets only as an index.
3. Scale the full viewport to fit; never crop half the page and mistake the contact-sheet boundary for live clipping.
4. Inspect native screenshots whenever text is too small in the sheet.
5. Record which images were reviewed and the visual findings.

A report with `screenshots > 0` but no recorded pixel review is incomplete, not passed.

### E. FRP protected-regression checks

Apply the specific release contract. For the automatic catalogue presentation plugin, verify at least:

- all fixed category and product routes return healthy pages;
- desktop, mobile, footer and mobile-footer catalogue grouping match;
- `Shop All` points only to `/products/`;
- required category/product IDs are present once and correctly grouped;
- public Hetron text/card/link/file routes remain unavailable as required;
- Derakane v2 page and API work and old links are absent;
- FNPT remains in the approved parent category and carries only approved resin behavior;
- source product guides remain unchanged before activation and transformed exactly after activation;
- authenticated historical downloads remain available only where commissioned.

Do not generalize these catalogue-specific checks to unrelated plugins; define that plugin's own protected contract.

## 4. Pass/fail rules

Fail the release for any:

- visible warning, notice, deprecation, stack trace or server path;
- HTTP non-200 on a required page;
- blank/fatal page;
- unapproved redirect;
- broken required image;
- console or failed-response error that affects the release contract;
- horizontal overflow above the allowed threshold;
- missing, duplicated, reordered or misdirected navigation/content;
- protected-content leak;
- unreviewed required screenshot;
- mismatch between installed state, DOM/HTML and pixels.

If activation has a commissioned emergency deactivation route, let that named tool execute it exactly once. Never improvise a rollback.

## 5. Evidence artifact

Issue one reviewed result file containing:

- release/plugin/version;
- plan/result hashes and replay-lock status;
- routes and viewports;
- uncached warning/path counts;
- DOM metrics and issue counts;
- screenshot paths and explicit pixel-review result;
- protected-regression results;
- final live installed state;
- any failed or superseded plans that remain locked;
- counts of email, Zoho, product, order, customer and unrelated website side effects.

Append a receipt immediately citing that reviewed result file. Keep the raw automated report and full-resolution screenshots beside it.

## 6. Long-run discipline

If the audit will exceed about two minutes or twenty pages, start it through the FRP Depot job runner and end the turn. Never block the lane with repeated waits. The job must write its report and receipt; the job watcher reports completion.

## Proven failure this prevents

On 2026-08-14, an automated audit saved screenshots and reported layout metrics but did not inspect their pixels. Its text gate checked only a few fatal phrases. A visible `Undefined property: stdClass::$post_parent` warning, partial server path and roughly 67px mobile overflow therefore survived the first report. The corrected release QA added broad runtime-error/path scans, desktop/mobile DOM checks and actual pixel review. This skill makes those steps mandatory.

## Completion checklist

- Commissioned deployment route only.
- Exact artifact and live installed state proved.
- Every affected route uncached and scanned.
- Desktop and phone DOM checks passed.
- Full-page and interaction screenshots captured.
- Pixels actually reviewed and recorded.
- Protected regressions passed.
- Reviewed result file written.
- Receipt appended.
- No unrelated website, WooCommerce, Zoho, email or customer-record write occurred.
