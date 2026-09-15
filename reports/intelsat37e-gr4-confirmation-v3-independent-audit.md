# Intelsat 37e confirmation v3 — independent fail-closed audit

**Verdict: FAIL_CLOSED (highest severity: HIGH).** The substantive data result passes independent validation, but the formal v3 evidence does not satisfy all preregistered provenance gates.

## Result split

- **Packet/process result: PASS.** The baseline output is empty. Both selected identity outputs are byte-identical (`6cfe0876…d2d2ef`), contain 26 complete records each, and all 26 are valid, unique, unfragmented IPv4 ICMP echo requests with correct IPv4 and ICMP checksums. All three null outputs are exactly empty. All six raw Docker return codes and inspected container exit statuses are zero.
- **Formal confirmation: FAIL_CLOSED.** Two HIGH provenance failures prevent accepting the project's `PASS` verdict.

## HIGH findings

1. **`V3-IN-CONTAINER-ARGV` — exact container argv is not recorded.** Every `commands.in_container_argv` begins with the input pathname. It omits argv[0], `/repo/work/gr4-packet-modem-official/build/apps/packet_receiver_file_audit_v3`, which is only recoverable from `--entrypoint` in the host Docker argv. The plan requires “exact host argv and exact in-container argv” and declares a missing manifest field fail-closed. Remediation: in a new versioned replay, record the resolved process argv as `[Entrypoint, *Cmd]`, plus inspected Docker `Config.Entrypoint` and `Config.Cmd`, before removal.

2. **`V3-BUILD-PROVENANCE` — reviewed sources are not bound to the executed binary.** The lock independently hashes the source inventory and final executable, but explicitly excludes `build/` and `build-portable/`. It freezes no compiler/linker identity, configure/build commands, compile argv, object/dependency hashes, link argv, build log, attestation or reproducible rebuild comparison. Thus the executed behavior is demonstrated, but the audit cannot prove that the reviewed preload/`VectorSource` source produced the executed bytes. Remediation: clean-build in the pinned image with the source mounted read-only; freeze complete build commands, toolchain, generated metadata, link inputs and final hash, preferably with a deterministic second rebuild or signed attestation.

## MEDIUM residual risk

**`V3-PREFLIGHT-OPEN-TOCTOU`.** Pre/post hashes catch normal drift, and the container sees a read-only repository mount, but the host can still alter or swap mutable path contents between preflight and process open and restore them before postflight. No such tampering was observed, and the artifacts correctly state `filesystem_immutable_claimed=false`. A content-addressed read-only snapshot/immutable mount should close this window.

## Gates that passed

- Chronology is coherent: plan file 00:16:42Z → v3 source 00:18:53Z → object/binary 00:21:58–59Z → runner 00:28:42Z → freeze 00:29:09Z → six non-overlapping runs in preregistered order 00:29:28–00:30:19Z → last manifest 00:30:21Z → report 00:30:32Z.
- The pinned image resolves exactly to `sha256:3272d31c7af3761ac3820e8ad7b2fce8fa7e6a45133ecc1acaf603fe321b54a5` (`linux/amd64`). An independent `ldd`/hash replay matched all 22 native-closure entries and canonical hash `69e2bf6c…aca57483`.
- All report/freeze and six manifest sidecars validate. Every audited regular-file SHA, byte count, device, inode and recorded mode matched. Frozen inputs and all run artifacts/manifests/sidecars are 0444; the frozen-input directory is 0555.
- All origin/snapshot pairs are byte-exact and distinct-inode. Baseline and both selected runs use the same identity snapshot: SHA-256 `dd15c23…9932292`, 4,773,064 bytes = 596,633 cf32 samples.
- Runtime parameters are correct: bins `128`, threshold `9.5`; baseline Costas `.02/.01/.005`; selected/null `.2/.1/.05`; one executable SHA across all runs.
- Source review confirms v3 reads every cf32 byte directly into `vector<complex<float>>`, moves it into the existing finite `VectorSource`, sets `repeat=false`, and leaves `PacketReceiver`/CRC/PDU routing intact. `VectorSource` copies elements and returns `DONE` exactly at end-of-vector; all six formal processes exited cleanly. Because of the separate build-provenance failure, this is a statement about reviewed source, not proof of executed-binary derivation.
- Historical v2 failed evidence remains present: the v2 independent audit is SHA-256 `8dd5e0c6…edfe1f5`, verdict `FAIL`, findings `NULL-EXIT-GATE` and `RUN-PROVENANCE`. The older v2 self-report remains `PASS`; v3 correctly does not erase the independent failure.

## Independent parser

The audit used a separate parser, not `run_confirmation_v3.py`. It checked complete big-endian uint32 framing, IPv4 version/IHL/total length/header checksum, MF and fragment offset, ICMP protocol/type/code/checksum, terminal offset and per-packet SHA-256 uniqueness. Results: baseline `0`; selected `26/26 valid/26 unique` twice with identical 944-byte files; three nulls `0`; no framing or protocol errors.

No fresh formal replay was performed: it cannot repair immutable historical manifest/build-provenance omissions, and the existing bytes were sufficient to validate the substantive output claim.

Machine-readable evidence: `reports/intelsat37e-gr4-confirmation-v3-independent-audit.json`.
