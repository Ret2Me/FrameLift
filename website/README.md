# Telemetry Yield — research preview website

Static Polish content site. Open `index.html` directly or serve this directory with a static server. No build, package manager, remote font, analytics, upload or backend dependency. It does not expose a public decoder release or promise a license.

## Included assets

The frozen paper PDF is included at `assets/paper.pdf`, its independently buildable manuscript source bundle at `assets/paper-source.zip`, and aggregate result summary at `assets/summary.json`. The ZIP is manuscript source, not a decoder release or raw benchmark archive. Do not replace the snapshot with live campaign output without updating every claim consistently.

Snapshot: 2026-09-12T22:52:19Z, 189/266 complete, 4541 progressive frames, external union 2998, additional 1547, missed 4, net +51.47%; 107 observations with additional frames. Signal-labelled subgroup 16/24, 136 vs 87, 49 additions and no misses.

The landing page deliberately distinguishes the evaluated audio path from experimental generic IQ/protocol capabilities. CLI examples match `docs/progressive-decoder.md`. Source checkout is a prerequisite; no imaginary public repository is linked.

## Verification interfaces

- `#command`: selected literal CLI text.
- Native radio inputs `name=mode`, values `quick`, `deep`, `full`.
- `#mode-description`: visible scheduling/resume explanation.
- `#copy-command`: clipboard action, progressively enabled.
- `#copy-status`: reserved polite status; denied clipboard selects text and explains manual copy.
- `#wyniki`, `#metoda`, `#uruchom`: section anchors.
- Test 1440px and 375px viewports, keyboard radio navigation, reduced motion, file URLs, clipboard denial and all packaged links.

Runtime visual tokens live in `styles.css :root`. `DESIGN.md` records their intent and scope. No UI contract is required for this marketing page.

## Checked result

`qa/results.json` records passing Chromium checks at 1440/768/390/320px, keyboard interactions, clipboard success/denial and no-JavaScript behavior. `verification/` contains static design audit and design-lint results. This is not a complete WCAG certification or cross-browser support guarantee.

`QA.md` records the final additional verification, including exact paper/JSON asset matching and source-ZIP rebuild. `verify-browser.cjs` accepts an installed Playwright module and Chromium executable. These are verification-only dependencies; its temporary server binds only to loopback and closes after the test.

```sh
node verify-browser.cjs /path/to/playwright-core /path/to/chrome
```

For private HTTP preview, run `node preview.cjs` and open the printed loopback URL. Only the six public page/assets paths are served; there is no upload or decoder API. The preview is not a public deployment.

Never expose the workspace or research archive as a web root. Any future public deployment requires a separate release decision.
