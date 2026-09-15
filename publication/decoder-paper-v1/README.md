# Telemetry Yield — preliminary IEEE Access manuscript

This is an English research draft, **not a submitted, accepted, peer-reviewed, or publication-ready article**. It uses the official IEEE Access LaTeX class/template downloaded from IEEE's author page (template revision 2026-05-13). Draft-only running headers replace template publication furniture so that no volume, accepted date, DOI, or IEEE endorsement is fabricated. Author names, affiliations, correspondence, biographies, funding and contributor declarations remain to be supplied by the human authors.

## Deliverables

- `telemetry-yield-preliminary-paper.pdf`: compiled paper.
- `main.tex`, `references.bib`, `Makefile`: editable manuscript and build recipe.
- `METHODS-AUDIT.md`: verified implementation boundaries and code locations.
- `evidence/summary.json`: immutable headline snapshot.
- `evidence/observations.json`: per-observation exact packet sets, counts, identities and timings (local research evidence, not a public data release).
- `evidence/checksums.sha256`: snapshot checksums.
- `verify-evidence.cjs`: independent snapshot consistency checks; does not run a decoder.
- `sources/`: downloaded reference documents and official template, retained separately with their original provenance.

## What the current paper can claim

At **2026-09-12 22:52:19 UTC**, 189 of 266 planned CANVAS recordings had complete five-arm results. The primary progressive receiver produced 4541 per-observation unique AX.25 UI frames versus 2998 in the union of controlled Dire Wolf and gr-satellites replays: 1547 additions, four misses, **+1543 net / +51.47%**. This is not a recall estimate against every transmitted packet, a measurement of globally new telemetry, or a comparison with every native SatNOGS processing path.

The frozen waterfall-confirmed subgroup contains 16 completed recordings out of 24 planned: 136 versus 87 frames, 49 additions and no misses. It is an exploratory metadata-labelled subgroup, not independent proof of spacecraft identity.

The separate codec-enabled and codec-disabled experimental receivers have **identical packet sets in all 189 recordings**, 4703 each. The codec hypothesis currently has no incremental union-packet yield in this snapshot. The experimental portfolio and progressive portfolio are different; do not use their different totals as a codec ablation.

The campaign continues independently. Later completed observations do not silently update this draft. Create a new numbered manuscript/snapshot version for new results. The snapshot generator refuses to overwrite existing evidence.

## Rebuild

On Ubuntu with TeX Live, dependencies used here are `texlive-latex-base`, `texlive-latex-recommended`, `texlive-fonts-recommended`, `texlive-latex-extra`, `texlive-publishers`, `poppler-utils`, `make`, and `unzip`. Node is used only for publication/website evidence tooling, not as a receiver runtime.

From this directory:

```sh
make
make verify
node verify-evidence.cjs
pdfinfo telemetry-yield-preliminary-paper.pdf
```

The Makefile points to the unmodified official template's class/font files under `sources/ieee-template/ACCESS_latex_template_20260513/`. It does not require a decoder rebuild. The downloaded IEEE template may retain its own sample sources and original distribution notices; they are not authored by this project.

The official title constructor emits two empty 9.2679-pt overfull-box warnings. They are retained and explicitly whitelisted by `verify-pdf-log.cjs` after visual inspection of page 1; other overflows, TeX errors and unresolved references fail verification. This is not claimed to be a warning-free or fully tagged/accessibility-certified PDF.

The data snapshot can be re-checked against the live local archive with `node verify-evidence.cjs --local`. This validates committed summary identity and common-input binding, not every raw waveform/FCS record; the registered campaign analyzer remains the authoritative final artifact-level validator.

## Required before submission

1. Complete all 266 planned outcomes, preserve missing/timeout outcomes, and run the registered raw-artifact yield and cost audits.
2. Report dependence-aware uncertainty and missingness on the finished cohort. Do not treat packets or overlapping passes as independent samples.
3. Preserve the disclosure that this is a historical repeat after earlier partial results were inspected. Add separately frozen evaluation data for independent validation rather than calling this cohort unseen.
4. Run matched frontend/timing/nearest-anchor/blind/multi-anchor ablations; separately isolate residual and codec effects. Preserve null results.
5. Independently examine added and lost packets and run full-bank false-acceptance controls. CRC is not spacecraft authentication.
6. Qualify broader mission/modulation claims on appropriate IQ/audio; the present result only covers the stated CANVAS post-FM link.
7. Complete environment/source reproducibility, public artifact availability decisions, author details, biographies, contributor/funding/conflict declarations, and human scientific review.
8. Recheck the target journal's current instructions and template, disclose AI contributions, and perform a focused novelty/literature review. Nothing has been submitted to IEEE.

## WA8LMF reference: deliberately separate

The supplied Dire Wolf PDF is dated January 2019 and compiles heterogeneous operator tests. The WA8LMF medium concerns 1200-baud packet-radio testing and distinguishes audio conditioning and tracks. **We have not run Telemetry Yield on that medium in this task.** Its published counts are not numerically comparable to these 9600-baud CANVAS counts.

Primary sources:

- [IEEE Access author guidance and template](https://ieeeaccess.ieee.org/authors/preparing-your-article/)
- [Official template download](https://ieeeaccess.ieee.org/wp-content/uploads/2026/05/ACCESS_latex_template_20260513-1-1.zip)
- [Dire Wolf WA8LMF compilation](https://github.com/wb2osz/direwolf/blob/dev/doc/WA8LMF-TNC-Test-CD-Results.pdf)
- [WA8LMF test medium and track descriptions](http://wa8lmf.net/TNCtest/index.htm)
- [gr-satellites command-line input contract](https://gr-satellites.readthedocs.io/en/latest/command_line.html)

No new licensing grant, commercial terms, public repository, or deployment support promise is inferred by this draft or the companion website.
