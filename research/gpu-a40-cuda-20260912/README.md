# A40/CUDA research bundle

This is a research and implementation-design artifact, not a CUDA implementation or GPU benchmark. The Polish report distinguishes current source behavior, frozen CPU results, published prior art, and untested proposals.

## Contents

- `telemetry-yield-a40-research.pdf`: readable report.
- `report.md`: editable report with numbered footnotes and primary-source inventory.
- `code-audit.md`: source-level mapping of GPU opportunities and exactness risks.
- `sources/raheli-psp.pdf`: primary author-hosted background lecture; provenance is in the report.
- `checksums.sha256`: report and audit integrity checks.

The main recommendation is a separate batched GPU backend, followed by a controlled uncertainty-aware channel-metric experiment for post-FM audio and a separate coherent GMSK experiment on IQ. These are proposals; no first-in-literature or universal superiority claim is made.

## Evidence boundaries

The host check found no exposed NVIDIA device, `nvidia-smi`, or `nvcc`. No drivers, CUDA toolkit, or GPU compiler were installed. No GPU timing or packet-yield measurement was performed. The ongoing frozen decoder campaign and its executables were not changed by this research.

CPU results refer to the immutable 189/266-observation snapshot at `../../publication/decoder-paper-v1/evidence/summary.json`, cutoff 2026-09-12T22:52:19.141Z. Later campaign results must not silently replace these numbers. The snapshot is not an independently unseen holdout or a raw-artifact FCS revalidation.

## Toolchain provenance

Read-only remote HEAD observations on 2026-09-12 are recorded below to identify the versions inspected. They are not validated dependency locks and do not imply that any of these packages was installed or tested on A40.

| Project | Remote | Observed HEAD |
|---|---|---|
| cuda-oxide | https://github.com/NVlabs/cuda-oxide | `6abfaa091e29a6275c1943895bfbc97efa306e98` |
| Rust-CUDA | https://github.com/Rust-GPU/rust-cuda | `6a836d9236fc38e0fa7a71f7bdeda7a8f82bc8d5` |
| cudarc | https://github.com/chelsea0x3b/cudarc | `5df8c19d0013566f5c275fa06cdf8d44c1b1171c` |

In particular, cuda-oxide is alpha. Its current README and older installation documentation describe different driver minima; qualify one pinned revision on the actual host instead of combining requirements from different revisions.

## Rebuild

Requires Pandoc, pdfLaTeX/TeX Live and Palatino (`mathpazo`) fonts. Run `make` from this directory. For integrity checking, run `sha256sum -c checksums.sha256`. PDF links to local supporting files assume this directory structure is preserved. The report is a research note, not the IEEE manuscript; the separate preliminary manuscript is under `../../publication/decoder-paper-v1/`.

The report was reviewed against the source audit. PDF layout was checked by rendering representative pages, including equations; this is not a tagged-PDF accessibility certification.
