# FrameLift source import — 2026-09-15

This import publishes the existing receiver and research documentation under the
FrameLift repository name. It is not a new decoding experiment, hardware GPU
qualification, or peer-reviewed release. Historical artifacts and executable
names retain the Telemetry Yield identity.

## Scope

Included: receiver and companion tools, CPU/optional-CUDA code, retained research
sources, test fixtures, configuration examples, documentation, narrative reports,
website sources, manuscripts, checksum-bound evidence and comparison figures.
The compact decoded telemetry behind the CANVAS comparison is included as well.

Excluded: raw capture archives, build outputs, virtual environments, local frozen
runtimes, working databases, bulk experiment outputs and local knowledge caches.
Exclusion from Git does not delete the local material. Historical documents can
refer to those laboratory-only paths; they are not promised as clone contents.

## Local verification before import

| Check | Result |
|---|---|
| `cargo xtask check` | Passed: maintained-code formatting, strict Clippy, CPU tests, Rustdoc and CUDA host checks |
| Receiver library | 444 passed, 6 explicitly ignored |
| CLI integration | 20 passed |
| `cargo xtask research-check` | Passed; retained experiment targets still emit existing warnings |
| `cargo test --locked --example paper_evidence` | 17 passed, including exact checked-in figure reproduction |
| Independently locked Meteor image audit | 1 passed |
| CANVAS comparison data checksum manifest | All seven listed files passed |
| Source inventory secret-pattern scan | No findings; suspected values are never printed |

Secret scanning is a bounded pattern-based check, not a proof that every possible
secret format is detectable. Binary fixtures are retained byte-for-byte; the
existing scanner also checks text members of ZIP archives. Hash-bound publication
and research snapshots explicitly disable Git newline normalization.

The import uses staged Git blobs rather than rereading mutable working files for
upload. The final remote tree must match the local staged tree before the branch
is advanced. Local checks do not establish a successful remote CI run.
