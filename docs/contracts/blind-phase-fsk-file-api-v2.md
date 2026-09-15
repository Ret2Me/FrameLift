# Blind phase FSK file API v2

## Boundary

`telemetry-yield-blind-phase-fsk-file-api-v2` is the stable batch boundary for
the clipping-robust, signal-selected binary phase FSK/GFSK/GMSK receiver. It
accepts one headerless interleaved little-endian signed-int16 I/Q regular file
and emits one atomically published, no-clobber JSON document. The current protocol adapter
is deliberately narrow: repeated HDLC flags, complete unstuffing, CRC-16/X.25,
and strict AX.25 UI structure. This API does not infer CCSDS, modulation, sample
rate, baud rate, Doppler state, or a satellite identity.

The API version freezes the input semantics and defaults. Incompatible changes
require a new API and result-schema identifier. Its formal result contract is
`schemas/blind-phase-fsk-file-result-v2.schema.json`.
The recorded adapter identifier is
`hdlc-crc16-x25-ax25-ui-plain-and-g3ruh`, explicitly binding both line-code
hypotheses rather than implying a plain-only path.
The machine-readable route advertises the exact upstream selection labels
`FSK`, `FSK AX25 G3RUH`, `GFSK`, and `GMSK`. These are routing aliases for the
same binary phase-discriminator family, not claims that modulation was inferred
from IQ.

## Required content identity

The caller must provide the exact input byte count and SHA-256 digest. The
worker rejects directories, devices, FIFOs, symbolic links, empty files,
incomplete CI16 pairs, size/hash disagreement, an output path equal to the
input, and unknown API versions. It snapshots device, inode, size, mtime, and
ctime before processing, rechecks them between phases, and rehashes content
before publishing output. An existing or concurrently created result is never
overwritten. Each result carries `result_payload_sha256`, computed over the
complete document except that self-hash. A retry verifies that hash plus the
source, effective configuration, attempt fingerprint, and implementation
closure. Because that self-hash is not an external attestation, v2 never trusts
it as proof that DSP already ran: a retry deterministically reruns selection and
decode, reconstructs the complete canonical result, and returns the existing
file only if every field is exactly equal. A semantically forged, relabelled,
CRC-inconsistent, consensus-inconsistent, malformed, mismatched, or corrupt
existing result fails closed. This observation-level recomputation is the v2
resume boundary; there is no within-observation
checkpoint. A failure leaves no new result document.

This detects accidental or concurrent mutation; it is not a substitute for an
immutable object store. Deploy workers with read-only input mounts and without
write permission to the source bucket.

## Reference-free selection and bounded memory

Candidate selection reads exactly one analysis window at a time. It stores only
fixed-size statistics in a temporary SQLite database, uses a 4 MiB SQLite cache,
forces temporary sorting to disk, and retains at most `candidate_window_limit`
selected records in memory. The database is removed on success and failure.
`maximum_scratch_bytes` is checked conservatively before scanning and against
the materialized database after commits and index construction.

The selector rejects analysis windows above 1,000,000 complex samples or larger
than the decoder window, and reports a conservative 64-byte-per-sample analysis
array envelope. The decoder then materializes one selected decoder window per
frontend pass. Preflight also computes a conservative working-set model for
the decoder. It includes a 192 MiB fixed runtime reserve; source, decimated,
retained-soft-symbol, and receiver-path allocations; and the product bounds
for candidate error units, map seeds, syndrome weight streams, and attempted
combinations. The coefficients are deliberately above the 260,988 KiB
development replay high-water mark for its frozen configuration; that replay's
model is larger than its observed RSS. Configurations whose model exceeds 512
MiB are rejected, and the exact model value is recorded in every result. This
is a conservative admission model, not a substitute for cgroup measurement.
The file API
also rejects decoder windows above 5,000,000 complex samples, more than
4,096 selected windows, timing banks above 65,536 hypotheses, and per-start
list-search budgets above 2,000,000 attempts. It also rejects more than 20,000
frame detections and serialized results above 128 MiB, which keeps completed
result verification bounded. It also rejects more than 20,000 pre-clustering
receiver paths in any window and checks that limit before appending the next
path. Rates with at least two source
samples per symbol are accepted; deterministic integer decimation targets at
least two actual samples per symbol, so 19.2 kbaud at 57.6 ksample/s is valid.
These are hard safety bounds, not
performance recommendations. The result reports selector cache/scratch use,
analysis and decoder window sizes, hypotheses, list attempts, and candidate
counts.

The repair scheduler is additionally capped at 50,000 attempts per region,
100,000 per receiver path, 400,000 per event, and 1,600,000 per selected
window. At most 16 events enter repair, at most 8 unique repaired frames are
materialized by one path, at most 8 are retained per event, and at most 16 per
window; event clustering is limited to
16 symbols. Preflight also rejects configurations whose declared window bank
could exceed 51,200,000 repair attempts or 512 repaired outputs in one file.
These product bounds prevent individually plausible fields from multiplying
into an unbounded job.

All combinatorial inputs have explicit production maxima: 64 regions per
start, 512 deep least-reliable symbols, 16 deep flips, 4,096 map seed
states, and 64 values in each frontend hypothesis list. A path's effective
attempt allowance is the minimum of the configured deep-search, path, event,
and remaining window budgets; a larger outer budget can never silently raise
the configured deep-search limit. The list decoder stops before accumulating
more output frames than its explicit path budget, and the outer receiver fails
closed before exceeding its global detection limit.

The three legacy short-list fields remain serialized for configuration
compatibility but are reserved in API v2 and must be exactly 64 symbols, 2
flips, and 20,000 attempts. They do not describe a second repair pass: v2 runs
an exhaustive zero-flip native pass and then the explicitly bounded deep
repair pass. Non-default short-list values fail preflight so an ineffective
knob cannot change the configuration hash while leaving DSP unchanged.

Scratch use is bounded, but not constant in capture duration. Provision it from
the preflight estimate and keep it on a quota-controlled filesystem. Memory is
bounded independently of capture duration. An interrupted observation with no
completed result retries from the start; campaign supervisors resume by
verifying and skipping completed immutable-observation results.

## Trust semantics

Every emitted detection passed the four recorded validation layers. Only a
frame requiring no list-repair is labeled `native_trusted_uncorrected`. A
CRC-valid repaired frame remains
`crc_valid_list_repaired_candidate_not_authenticated`, even when two declipping
or phase-difference paths agree: those paths reuse the same samples and are not
independent authentication. Plain and G3RUH hypotheses likewise reuse the same
IQ and never increase independent-frontend consensus. The `trusted` and `repaired_untrusted` arrays make
that distinction machine-readable.

The result also sets `publication_superiority_established` to false. A valid
file-API result establishes execution and provenance, not scientific
superiority. That claim still requires a frozen oracle-free holdout, same-IQ
baselines, negative controls, confidence intervals, and independent audit.

## CLI

Compute identity before dispatch, then run:

```bash
telemetry-yield decode-blind-phase-fsk /data/capture.raw \
  --api-version telemetry-yield-blind-phase-fsk-file-api-v2 \
  --output /results/capture.blind-fsk.json \
  --sample-rate 57600 --baudrate 9600 \
  --expected-size-bytes 121479168 \
  --expected-sha256 HEX_DIGEST \
  --scratch-directory /scratch/telemetry-yield \
  --maximum-scratch-mib 512
```

The default v2 frontend bank uses radius factor 3, phase-difference lag 1, and
both plain and G3RUH AX.25 line-code hypotheses.
Repeat `--constant-radius-factor` or `--phase-difference-lag` to provide an
explicit frozen bank; repeat `--descramble-mode plain|g3ruh` to override the
line-code bank. `--symbol-rate` is an alias for the required
`--baudrate`; repeat `--rate-error-ppm` for a bounded clock-rate bank around
that nominal symbol rate. If the nominal rate itself is unknown, dispatch
separate content-bound attempts with an externally frozen finite rate list;
v2 does not silently search an unbounded range. The result schema freezes every
effective-configuration key and type; the canonical configuration hash,
effective defaults, and source-module hashes are included in the result and
its attempt fingerprint.

## Exit and retry behavior

Exit zero means a complete, schema-versioned result was published; it does not
mean telemetry was found. Invalid input, resource bounds, mutation, DSP failure,
or publication failure raises an error and exits nonzero. Retry operational
interruptions with the identical input and arguments. Do not retry malformed
input or increase scientific search budgets after observing a holdout result.

A valid, nonempty, exactly all-zero CI16 capture completes without entering DSP.
Its result sets `degenerate_input=true`, `degenerate_reason=all_zero_ci16`, and
reports zero selected windows, decoded windows, and detections. This explicit
terminal semantic permits frozen all-zero null exposure to be counted without
pretending that timing recovery ran; malformed size/content identity still
fails closed.
