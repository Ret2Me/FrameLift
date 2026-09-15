# Documentation and manuscript verification

Date: 2026-09-15. Scope: repository documentation, the version-2 manuscript and
its reporting tool. No new field decoding or receiver qualification is claimed.

## Checks completed locally

| Check | Outcome |
|---|---|
| Evidence export | 266 complete rows; exact-set differences matched to final yield analysis; time sums matched to cost analysis |
| Frozen cohort metadata | All 266 IDs, signal labels, dates and station identifiers agree with the frozen cohort |
| Portable bundle verification | Required file checksums and count/subgroup arithmetic pass |
| Reporting-tool tests | 10 passed, none ignored; includes corrupted bytes, missing manifest entry, duplicate ID, inconsistent totals and deterministic assets |
| Reporting-tool Clippy | `cargo clippy --locked --example paper_evidence -- -D warnings` passed |
| Reporting-tool formatting | Rustfmt check passed |
| Paper build | pdfLaTeX / BibTeX / two final pdfLaTeX passes; shell escape disabled |
| Final typesetting log | No unresolved citations/references, missing glyphs or overfull boxes; underfull spacing notices remain |
| PDF | Five letter-size pages; all 14 fonts embedded; no JavaScript or encryption |
| Visual inspection | Pages 1, 2, 4 and 5 checked, including equations, diagram, table, chart and bibliography |
| Links | Current entry-point documentation checked for missing local targets |
| CI configuration | YAML parsed; read-only permissions retained; paper evidence/test commands added |
| Receiver preservation | 131 existing Rust runtime/test source files match the September 14 inventory |
| Earlier evidence | Version-1 evidence checksums still pass; earlier manuscript/evidence not rewritten |

The CI commands were executed locally. No remote CI run, public repository push,
paper submission or website deployment was performed.

## Key frozen identities

| Artifact | SHA-256 |
|---|---|
| PDF | `a441168fa1e88734c7e4e1f3125a2821b6775309803247dddd8434057fda25fe` |
| Evidence checksum inventory | `576108cca982a4f08c5ccefcc10e8a6958ea60789579a07c50192ce9aeccfcae` |
| Evidence summary | `92e015ea7a41ca64b74e3d8195a190f2db5ce5f0e6db59fd9404ec8c554629ae` |
| Per-observation reporting rows | `8fa475c951df134c1c8f085f487fddf60f8a0c8bb65178221855dfdfca9b4aba` |
| Frozen source cohort | `291d773cca67ba4a108b43338e6592974fe2c915717f92ef0bd352109b2ac1a9` |
| Generated SVG | `effbac6ed40af20e8b87b750b558a55323204a5c26d63af9f020391b2d85a84c` |

The PDF checksum describes this build; rebuilding may change PDF metadata and its
checksum without changing evidence. The SVG, metrics and table were tested for
deterministic generation from the same verified bundle.

## Source review

Method details were checked against current Rust source, the historical methods
audit and frozen experiment documents. The Graphify index mainly covers earlier
Python research; it was used for orientation, not treated as evidence for current
Rust dependencies.

Competitor scope was checked against official project documentation. Titles,
authors and bibliographic coordinates of the three journal references were
cross-checked against Crossref DOI metadata; IEEE Xplore full-text access was
blocked by its browser verification page. WA8LMF context uses the retained
version-1 source copies where current fetches failed. No score on that test
medium is claimed.

## Remaining limits

This is a count-level reporting bundle, not the full raw waveform/FCS/task archive.
The publication-readiness and independence flags remain false. The full historical
cohort was not rerun on the current refactored receiver. CUDA hardware execution,
independent field validation, human authorship review and venue-specific submission
requirements remain outside this documentation update.
