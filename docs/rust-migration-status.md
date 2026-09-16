# Repository-wide Rust migration

Status date: 16 September 2026. **Incomplete.** The receiver runtime is native;
the entire repository is not yet Rust-only. The September 8 plan covered the
receiver and excluded the independent planning project. The current requested
scope includes the remaining tools and tests as well; that earlier exclusion
does not establish completion of the new request.

At the start of this work Git tracked 357 Python files: 122 under `src/`, 219
under `tests/`, eight scripts and eight numerical-oracle generators. Those
counts include real functionality and reference material. They are not a
measurement of runtime CPU use. No Python files have been hidden from Linguist
or deleted merely to improve the language bar.

## Native replacements added in this increment

| Legacy responsibility | Rust replacement | Scope |
|---|---|---|
| `models.py`, `canonical.py` effective config | `rust/research/config.rs`, existing canonical encoder | Typed config and Python-compatible fingerprint on the documented domain |
| `metrics.py` | `rust/research/metrics.rs` | Event-local unique-frame accounting and exposure rates |
| `events.py` | `rust/research/events.rs` | Conservative transmission-event matching |
| `splits.py`, `license_gate.py` | `rust/research/mod.rs` | Cross-split leakage and provenance gates |
| `prospective_selection.py` | `rust/research/selection.rs` | Outcome-blind metadata projection and diversity-capped cohort |
| `public_random_selection.py` | `rust/research/selection.rs` | Salt/rank derivation and pulse metadata/certificate binding, not signature authentication |
| `pcap_ipv4_audit.py` | `rust/research/pcap.rs` | Classic PCAP and length-prefixed IPv4/ICMP audit |
| `store.py` | `rust/research/store.rs` | Compatible SQLite table, atomic reservations and guarded completion |

These functions are exposed through the new `framelift-research` binary, not
Python wrappers. [Usage and compatibility limits](native-research-tools.md).
They supplement the existing native receiver, image tools, progressive runner,
comparison programs and paper-evidence tools. The historical Python commands
have not all been redirected or retired, so they remain available as references.

The September 16 increment adds `framelift-campaign`: native public SatNOGS
metadata acquisition, conservative exposure inventory, grouped cohort freezing,
executable registration, waveform acquisition, paired CPU execution and audited
reporting. This completes a supported **FSK9600 archival experiment path** without
FrameLift Python orchestration; it does not replace every historical acquisition
adapter or the planning application. External codecs/reference decoders retain
their own runtimes. [Workflow and boundaries](native-archive-workflow.md).

## Work still required before declaring full migration

1. Port the remaining S3/CAMRAS acquisition and dataset adapters beyond the
   new public SatNOGS audio workflow,
   including RML dataset handling, with captured-response and malformed-input tests.
2. Replace the remaining research orchestration, reporting/readiness commands,
   environment inventory and IQ/TLE diagnostics. Audit every Python CLI command
   against a native entry point rather than relying on filename similarity.
3. Port the independent `src/telemetry_yield/planning/` subsystem: 43 modules
   spanning scheduling, weather, probability, simulation, data models and
   adapters. It is a substantial remaining subsystem, not an interpreter shim.
4. Translate remaining tests and numerical-oracle generation to native tools or
   immutable, documented fixtures. Preserve independent expectations and avoid
   generating the expected result with the implementation under test.
5. Prove frozen campaign packet/FCS/provenance equivalence, restart semantics and
   single-/multi-worker equivalence after routing callers to the native tools.
6. Remove the Python package and old entry points only after their callers and
   acceptance gates have replacements. Preserve the exact historical source
   identities referenced by scientific evidence, without relabelling that
   archival step as an implementation port.

The acceptance condition is a clean build, test and documented research workflow
without Python, with supported behavior and evidence preserved. A lower GitHub
Python percentage alone is not an acceptance condition. Full field qualification,
GPU hardware parity and a new independent publication holdout remain separate
gates; this migration does not establish them.

## CI repair

The failed run compared a Morse timing score with bit-exact JSON equality.
The runner and local libm produced adjacent floating-point values, while the
decoded character, valid fraction and run count agreed. The regression now
checks those discrete outputs exactly and bounds the timing score to four ULPs,
with a separate test rejecting larger differences and invalid numbers. No
demodulation, threshold or packet-validation rule changed.

Fix commit: `1de0fe7340bfb556e472af6be19a1d879b0dfb9d`.
[Both quality and dependency jobs passed](https://github.com/Ret2Me/FrameLift/actions/runs/35020996678).
New migration changes must pass the same workflow; that earlier green run is
not evidence for later commits.
