# Verification — 2026-09-12

The website is a **local research preview**, not a hosted decoding service or public software release. It uses the same immutable 189/266-observation snapshot as the preliminary paper. This report does not mean the complete campaign has finished.

## Checks completed

- `node verify-browser.cjs /tmp/telemetry-site-qa.ULZifO/package /tmp/telemetry-paper-browser/chrome-linux/chrome`: **19 checks passed** in actual Chromium 117.0.5938.62 with Playwright Core 1.38.1. Machine-readable results are in `verification/browser-report.json`.
- Widths 1440, 1024, 768, 375 and 320 CSS px: no document-level horizontal overflow. The command block has its own accessible overflow.
- Keyboard: first-tab skip link with visible focus, native arrow-key mode selection, and Enter-operated setup disclosure.
- Actual clipboard write/read succeeds on loopback. Injected permission denial selects the command and provides a manual-copy fallback. The mobile console retains its height during that feedback.
- Reduced motion disables smooth scrolling and transitions. Controls continue working offline after loading. A direct local file URL also renders without JavaScript; unavailable enhancements are disabled or hidden and manual resume instructions remain available.
- All local fragment targets and PDF, ZIP and JSON links resolve. PDF and JSON asset hashes match the manuscript bundle. No external runtime request, console error or uncaught page error occurred.
- The four primary text/surface token combinations have contrast ratios between 5.49:1 and 14.55:1. This is a scoped contrast check, not a whole-site accessibility certification.
- Premium strict static audit: **0 errors, 0 warnings** in `verification/static-audit.json`. Official `designmd lint DESIGN.md`: **0 errors, 7 advisory warnings** in `verification/design-lint.json`. The warnings concern nonstandard palette naming and lack of frontmatter component references; actual roles and consumers are documented in `DESIGN.md` and runtime CSS remains the canonical token owner. No token generation is used.
- `node --check app.js` and `node --check verify-browser.cjs`: passed. Searches of shipped UI files found no native dialogs, false `href="#"` links, clickable nonsemantic handlers or `innerHTML` assignment.
- Desktop hero/results and the mobile hero were visually inspected. Full-page and viewport PNGs are under `verification/`. The style preserves the paper's instrument-like accounting of additions **and misses**, instead of implying universally better reception.

## Paper and downloadable source

- The five-page PDF builds with the official IEEE Access class. All listed font entries are embedded; there are no unresolved references or body overflows.
- `make verify` checks snapshot hashes and the TeX log. The class's two known empty 9.2679-pt title-box warnings are explicitly retained and whitelisted; other overflow warnings fail verification. All five pages were visually inspected.
- `node verify-evidence.cjs --local`: passed the 189-case five-arm set/count/timing check, subgroup accounting, local cohort membership, and SHA/common-input binding checks. It is not a replacement for the campaign's final raw-artifact audit.
- The downloadable source ZIP was extracted into a fresh temporary directory, rebuilt with `make`, and checked with `make verify`. Its extracted PDF text matches the canonical paper. The build log is `verification/source-rebuild.log`.
- The Forney bibliography entry uses BibTeX's surname/suffix syntax so the IEEE reference renders the author's name correctly.

## Scope and remaining risks

No Firefox/Safari, assistive-technology, physical touch-device or exhaustive zoom matrix was run. Chromium 117 is a local verification browser, not a declared browser-support floor. The PDF is not tagged or accessibility-certified. No externally hosted link or domain has been created.

The paper remains unsubmitted and requires final outcomes, dependence-aware analysis, matched ablations, false-acceptance checks, author details and human scientific review. WA8LMF is cited as benchmark methodology; no Telemetry Yield score on that 1200-baud test is claimed. Nothing in the site asserts a license, a production support contract or IEEE endorsement.
