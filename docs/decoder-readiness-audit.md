# Rust decoder control-suite gate

`examples/decoder_readiness_audit.rs` checks an explicitly selected positive/negative control suite before expensive final experiments. It never decodes samples, edits an existing campaign, downloads data, or declares a manuscript ready. It validates source bytes and frozen result bytes, reconstructs verified strict AX.25 UI PDU sets, and compares them to exact expectations.

## Contract and limitations

- `control_suite_passed` requires every listed run to pass and at least one passing positive **and** negative case for each required receiver. Empty suites cannot pass. This is a minimal control gate, not a statistically adequate sensitivity/false-alarm campaign.
- `publication_ready` and `deployment_ready` are always `false`. Held-out evidence, scientific novelty, uncertainty, operational checks, authorship and publication conditions need separate assessment.
- Expected PDU hex is **without FCS**. Emitted full-frame hex must retain the **received** FCS, which this audit independently recomputes using a bitwise CRC-16/X-25 implementation. Receiver-provided `crc_passed`, candidate counts or `publication_ready` fields are not trusted as proof.
- The narrow structural contract matches the current project validator: 2–10 legal AX.25 addresses, control byte `03`, a PID byte and optional information. Other AX.25 control forms (including a set P/F bit), CCSDS, CSP, SSTV and non-UI candidates are outside this control gate. This is not a universal telemetry validator.
- Every source identity and JSON artifact is bound by its absolute path, lowercase SHA-256 and nonzero byte count. Actual source contents are hashed. JSON is hashed and parsed from the same bounded read, avoiding a hash/reopen substitution. JSON is limited to64MiB, regular files only. Source path, hash and bytes in the decoder result must match the control; relocation requires an explicit new manifest/provenance decision.
- Ground truth is a **trust boundary**: the experiment owner supplies the expected bytes and exact-input provenance. The tool cannot infer independence or prove that a PDU genuinely originated on air. `independent_exact_input` means a separately verified decoding of this very source, not merely a live station packet from the same observation. A live IQ frame must not become an OGG recovery requirement until recoverability from those exact OGG bytes is independently established. `synthetic_exact_input` refers to a known encoded waveform; `known_negative_input` to a characterized no-frame input. Parser fixtures are not DSP controls.
- Receiver labels and source declarations identify an owner-approved experiment; this version does not independently attest the executable used for basic/innovation runs or establish numeric equivalence of separate input transformations. Preserve the run plans/executable hashes in the scientific experiment package. Progressive session input and executable-hash declaration are cryptographically bound through its existing session digest, not externally authenticated.

## Supported frozen result formats

| Manifest format | Accepted decoder result | Frames used | Source/completion checks |
|---|---|---|---|
| `native_audio` | `rust-native-audio-result-v1` | `frames[].frame_with_fcs_hex` | `source_input`; `status=complete`; zero failed windows and positive window count |
| `innovation` | `innovation-audio-result-v1` or `v2` | `report.union_full_frames[]` | `source`; `status=complete`; positive samples/sample rate |
| `progressive` | `progressive-audio-result-v1` | `frame_with_fcs_hex[]` | Required SHA-bound `progressive-audio-session-v2` manifest; its `audio.source`; recomputed typed-JSON session digest; `complete=true`; matching positive completed/total task counts and positive window count |

The count fields must agree with the independently parsed unique set. Missing frame arrays, duplicate union entries, malformed hex, non-UI frames, invalid received FCS, mismatched source, partial status, absent files and mismatched hashes fail closed. Unavailable/invalid results have `verified_pdu_hex: null`, not a misleading successful empty set. A valid completed output with the wrong PDU set reports missing and unexpected PDUs separately.

## Manifest

All manifest objects reject unknown fields; enum spellings are exact. `session_manifest` may be omitted or null except for progressive, where it is required. Below is a schema illustration, not a runnable control or a source of real expected bytes:

```json
{
  "schema": "decoder-control-suite-v1",
  "suite_id": "frozen-controls-release-name",
  "required_receivers": [
    {"label": "rust-basic-frozen-build", "format": "native_audio"}
  ],
  "cases": [
    {
      "id": "known-positive",
      "kind": "positive",
      "source": {"path": "/absolute/control.wav", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX", "bytes": 123},
      "expected_pdu_hex": ["REPLACE_WITH_VERIFIED_FCS_FREE_AX25_UI_HEX"],
      "expectation_basis": "synthetic_exact_input",
      "expectation_note": "Describe independent encoder/reference, exact waveform identity and how expected bytes were established before testing this receiver.",
      "outputs": [
        {
          "receiver": "rust-basic-frozen-build",
          "artifact": {"path": "/absolute/positive/result.json", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX", "bytes": 456}
        }
      ]
    },
    {
      "id": "known-negative",
      "kind": "negative",
      "source": {"path": "/absolute/noise.wav", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX", "bytes": 789},
      "expected_pdu_hex": [],
      "expectation_basis": "known_negative_input",
      "expectation_note": "Describe controlled negative waveform generation, seed and receiver search exposure.",
      "outputs": [
        {
          "receiver": "rust-basic-frozen-build",
          "artifact": {"path": "/absolute/negative/result.json", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX", "bytes": 456}
        }
      ]
    }
  ]
}
```

Cases may target subsets of receivers, but every required receiver needs positive and negative coverage. A case cannot repeat the same receiver. Every listed output affects the final gate; unsupported or missing required control results must not be silently dropped.

## Run and exit status

```sh
rtk proxy /home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/control-gate-tests/decoder_readiness_audit_v1 --manifest /absolute/control-suite.json --output /absolute/new-control-audit.json
```

The report is a new file only; existing files are never overwritten. Exit0 means the listed controls pass, exit1 means a failed/invalid suite (with fail-closed report where writable), and exit2 means invalid invocation or inability to write the report. A malformed/unreadable manifest still generates `control_suite_passed:false` if the requested new output can be created. Exit0 does not imply scientific or deployment readiness.

For a normal Cargo environment:

```sh
rtk proxy /home/ubuntu/.cargo/bin/cargo test --example decoder_readiness_audit
rtk proxy /home/ubuntu/.cargo/bin/cargo run --release --example decoder_readiness_audit -- --manifest /absolute/control-suite.json --output /absolute/new-control-audit.json
```

This workspace can also link against its preserved Rust dependency artifacts without rebuilding the running receiver. The test/binary build used the1.98.1 toolchain and existing `target/release/deps` libraries; output binaries are under `work/decoder-readiness-20260911/control-gate-tests/`.

The unit tests exercise three real JSON shapes and adversarial cases including source/result mutation, wrong identity, partial status, missing output, bad FCS, valid-CRC non-UI data, false counts, duplicate frames, missing arrays, inconsistent ground truth, missing receiver coverage and a vacuous suite. An opt-in read-only integration test parses the preserved real public14967362 progressive result, verifies its session binding and rechecks received FCS/UI; it is only an adapter-compatibility check, not independent expected-output evidence or a passing scientific control suite.
