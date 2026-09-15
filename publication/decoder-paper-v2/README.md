# Telemetry Yield — complete-cohort research draft

[Read the PDF](main.pdf) · [LaTeX source](main.tex) · [Results](../../docs/benchmarks.md) · [Repository](../../README.md)

[Build and evidence verification record](QA.md)

**Version 2 · 2026-09-15 · unsubmitted, not peer reviewed.**

This paper explains the implemented receiver, its architecture and equations,
the historical five-arm experiment, all 266 completed observations, the separate
signal-labelled subgroup, compute cost, missed reference packets and limitations.
The manuscript uses the IEEEtran journal layout and numbered IEEE-style references.
It is not a claim of compliance with every submission requirement of a specific venue.

## Main result

The progressive receiver recovers 6,220 observation/PDU pairs compared with 4,066
in the tested Dire Wolf/gr-satellites union: 2,158 added and four missed, or 52.98%
net. In 24 metadata-labelled signal observations it recovers 174 versus 111, with
63 additions and no misses. The codec-enabled experimental arm adds no packets
to its codec-disabled control across the entire cohort.

The cohort was previously exposed. The paper therefore presents a retrospective
systems study, not an independent proof of universal superiority or algorithmic
priority. The last refactor and optional GPU path were not evaluated on this
whole cohort. Manuscript preparation did not rerun or modify the receiver trial.

## Build

Requires the repository-pinned Rust toolchain, Make, pdfLaTeX, BibTeX, IEEEtran,
TikZ, booktabs and microtype. On Debian/Ubuntu these TeX components are normally
provided by `texlive-latex-recommended`, `texlive-latex-extra`, `texlive-pictures`
and `texlive-publishers`.

```sh
# From the repository root:
make -C publication/decoder-paper-v2
make -C publication/decoder-paper-v2 verify
```

`main.pdf` is the output. The build verifies the evidence and regenerates metrics,
the results table and SVG from its JSON using Rust before typesetting. Numerical
macros avoid manually copying the main table values into LaTeX. No shell escape,
Python runtime, raw recordings or external reference decoders are needed to build.

## Evidence package

| File | Contents |
|---|---|
| [summary.json](evidence/summary.json) | Aggregate results, signal subgroup and final-analyzer qualifications |
| [observations.json](evidence/observations.json) | 266 count-level rows, timings, source/result hashes and source URLs |
| [receiver-freeze.json](evidence/receiver-freeze.json) | Historical build identities and recorded comparison settings |
| [grsat-profile.yml](evidence/grsat-profile.yml) | Actual replay profile |
| [protocol.md](evidence/protocol.md) | Historical optimized-repeat protocol |
| [prior-evaluation-exposure.json](evidence/prior-evaluation-exposure.json) | Disclosed prior evaluation |
| [analysis-registration.json](evidence/analysis-registration.json) | Final analyzer repair/version record |
| [checksums.sha256](evidence/checksums.sha256) | Compact-bundle checksum inventory |

The [Rust exporter](../../examples/paper_evidence.rs) checks the recorded exact
PDU sets against the final analyzer before exporting counts, and checks wall sums
against the cost report. The compact bundle does **not** contain the full waveform,
packet/FCS, log or task corpus. Its verifier checks hashes and arithmetic, not a
fresh raw-artifact audit or independent reproduction. Full runtime/task admission
remains separate, and publication/independence gates remain false.

[Reproduction levels](../../docs/reproducibility.md) explain which operations
require the full laboratory tree. Local absolute paths in frozen evidence are
provenance, not paths needed to build this PDF.

## Before submission

- Establish human authorship, affiliations, corresponding author, funding and conflicts.
- Complete the independent field evaluation and admission/false-acceptance audits.
- Review related work and narrow any algorithmic novelty claim to demonstrated evidence.
- Finalize public software/data release arrangements and a persistent artifact identifier.
- Apply the chosen venue's own template, length, data and disclosure requirements.
- Review every technical statement, figure, reference and generated-content disclosure.

The disclosure in the acknowledgment reflects the actual assistance used and the
[IEEE guidance](https://ieeeaccess.ieee.org/authors/preparing-your-article/).
No author name, affiliation, DOI, funding source or acceptance claim is invented.

## Version history

Version 1 is preserved unchanged in `publication/decoder-paper-v1`: it reports
an interim 189-observation snapshot. Version 2 replaces that partial evidence in
the current documentation with the completed 266-observation result. It also
documents the newer engineering scope separately from the older measured build.
