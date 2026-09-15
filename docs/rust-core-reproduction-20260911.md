# Isolated-source core reproduction, 2026-09-11

This package verifies the core Rust build and tests from an extracted source snapshot, separate from the working tree. It is an internal reproducibility check, **not** an independently reproduced publication, public release, full benchmark environment, or new software version.

The archive contains `Cargo.toml`, `Cargo.lock`, `rust/` (including embedded test fixtures), and `tests/fixtures/satyaml_registry/`. Version 2 additionally contains eight historical Python source files under `src/telemetry_yield/`: `clipping_robust_fsk.py`, `iq_declipping.py`, `clock_recovery.py`, `soft_sync.py`, `symbol_boundary.py`, `crc.py`, `ax25_validation.py`, and `blind_phase_fsk_file.py`. These are read as bytes for the frozen oracle's SHA-256 provenance checks, not imported or executed by the Rust tests. Keeping those references does not introduce a Python runtime dependency.

It deliberately excludes observations, credentials, production configuration, external GNU Radio/Dire Wolf installations, and benchmark outputs. Research examples/wrappers and the full five-receiver environment are separate artifacts and are not certified by this core-only test.

The initial v1 archive omitted these eight references. Its isolated library run therefore finished with 311 passed, two failed and five ignored tests; the two failures were missing-file errors in provenance checks. The original archive and failed log are retained. This is a packaging failure, not a passed reproduction and not evidence of a decoder regression. Version 2 must pass a new full run before being described as reproducible.

From a newly extracted directory:

```text
env RUSTC=/absolute/path/to/rustc /absolute/path/to/cargo test --lib --test cli_integration --locked --offline -j 2
```

The offline build uses this host's existing Rust toolchain and cached registry sources, with a fresh target directory inside the extracted tree. It does not require Python or FFmpeg for the selected regular tests. Five explicitly ignored, separate-fixture library tests remain ignored; do not describe them as passed. The CLI tests clear their child environments and use an empty executable search path, but that is not a kernel-enforced network sandbox.

Record the source archive digest, compiler/Cargo versions, exact command, complete test log, exit status and resource envelope. A successful test here establishes that the selected core can build and pass those tests without the original working-directory contents. It does not package every dependency for a machine without a populated cache or qualify all supported receiver modes on real satellite recordings.
