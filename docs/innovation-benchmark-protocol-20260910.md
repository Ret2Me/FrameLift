# Innovations receiver benchmark v2, 2026-09-10

Frozen before new-cohort decoder outcomes. This protocol measures recovery, not
scientific priority or publication readiness. Metadata-only cohort selection is
separately owned and frozen in `acquisition/cohort.json`; selected observations
remain in the denominator even when a download or receiver fails. Prior exposed
observations are development data, not a new holdout. Selection is not driven by
our decoded counts, waterfalls, archived frame bytes, or intermediate gains.

## Scope and exact shared input

The present complete comparison is mono 48 kHz Vorbis post-FM audio for the
metadata-qualified 9600 baud GMSK/FSK AX.25 G3RUH profile, not all modulation types
or the original station IQ chain. Whole recordings must be 0.2–1800 seconds.
One bounded conversion per observation is `ffmpeg -nostdin -v error -n -i OGG
-map 0:a:0 -c:a pcm_s16le -flags:a +bitexact -fflags +bitexact SHARED.wav`.
Bitexact output is required for the installed Dire Wolf WAV reader, which rejects
the otherwise emitted LIST metadata chunk. This container compatibility change
was discovered in the synthetic development smoke before any new-cohort decoding;
PCM data hashes matched between plain and bitexact outputs in that fixture.
No gain normalization, resampling, downmix,
manual timing selection, hand editing, or target CRC repair occurs. All five
receivers consume this exact shared PCM16 file. Hashes and conversion provenance
are retained, but owned temporary PCM is removed after processing.

1. Improved innovations v2, pooled codec mode, receives shared PCM16 plus original
   OGG codec metadata. It independently reconstructs PCM16 from the original and
   verifies sample equality. This extra codec information is explicitly part of
   the treatment, not a claim that all receivers see identical metadata.
   Codec model gates hold out **conditional residuals**, not untouched raw source
   PCM: source frontend normalization and CRC-baseline timing selection already
   see the whole source window. Do not label this a raw-PCM independent calibration
   holdout. Whole target-window exclusion guards still prevent using target data
   in codec calibration.
2. Frozen innovations v1 **without codec side information**, on shared PCM16.
   This is a counterfactual common-input baseline, not the entire earlier OGG
   codec-aware stack. Original-OGG v1/v2 regression is a separate development test.
3. Frozen complete progressive v3, including blind and multi-anchor banks, on the
   same PCM16. A bank not finished within the bound is incomplete, never zero.
4. Dire Wolf `atest -B 9600 -F 0 -h`, no repair, same PCM16.
5. Installed gr-satellites FSK9600/AX.25 G3RUH offline profile, same PCM16, telemetry
   submission disabled. This is not historical full gr-satnogs IQ processing.

## Execution and frozen identity

Default four observation workers, two DSP threads each (at most eight declared
DSP threads). Each receiver is a separate timed child process and has a 900-second
wall safety bound. Full progressive internally stops 20 seconds earlier to allow
owned worker cleanup. All receiver orders rotate cyclically by fixed cohort rank.
Startup is included; conversion is measured separately. Outer child wall, user
CPU, system CPU and peak RSS are recorded. Concurrent host timing is not isolated
latency, equal compute, or superiority at a fixed budget. Timeout CPU may be
incomplete and is marked as such.

The runner records SHA256 of itself, the improved and historical binaries,
ffmpeg/ffprobe, atest, time, gr-satellites launcher and Python/shared-object package
files, profile, protocol and frozen cohort. This is not a complete operating-system
or all shared-library runtime closure. Resume requires the same frozen runner,
receiver binaries, protocol, cohort and resource policy. Never rebuild a running
frozen executable. No downloaded payload byte is fed to any training/search.

Each arm has immutable attempt directories and an atomically published commit
pointer. Completed arms are skipped only after hashes and frame scores revalidate;
failed attempts remain available and require `--retry-failed`. Lost host power can
leave an uncommitted attempt, which is preserved and retried. An exclusive file
lock prevents concurrent runners using the same output. New PCM scratch uses an
owned temporary directory; only that directory is automatically removed.

`--max-new-observations 100` is an operational milestone on the already frozen
500-observation selection, not a result-dependent stopping rule. Resume without
that flag continues the same cohort and protocol. All summaries identify selected,
not attempted, incomplete, per-arm complete, and all-five-complete denominators.
Attempted incomplete observations produce a nonzero runner exit; an intentionally
unattempted milestone remainder alone does not. Resume also checks observation and
arm labels against their selected IDs and owned directories, not hashes alone.
Five completed arms cannot override a final observation-level input/runtime/cleanup
failure. The scorer also checks that every completed arm and the observation
receipt agree on one PCM hash and size, including resumed runs whose temporary
file has been removed. Cohort validation rejects a mismatched CANVAS transmitter,
NORAD, mode or baud profile and a freeze preceding selected observation completion.

## Scoring and statistical unit

Primary unit: exact `(observation ID, FCS-stripped strict AX.25 UI PDU bytes)`.
Duplicate detections, timing hypotheses, overlapping windows and codec lanes do
not count as additional telemetry. Also report globally distinct PDU counts and
both added and missed PDU sets; additive receiver unions do not imply intrinsic
single-detector dominance. Global “added somewhere” is distinct from absent from
the comparator everywhere and must not be described as globally new telemetry.

Native frames carry the received FCS and are rechecked with independent bitwise
CRC and the common strict UI structural filter. External outputs strip FCS; their
CRC status is attested by the configured decoder, not reconstructed with a
synthetic checksum. Malformed/truncated outputs fail closed. CRC is not transmitter
authentication or a proof of zero false positives; this field cohort is not a
trusted all-noise false-alarm qualification.

Five-way comparisons use only observations complete for every arm, alongside all
missing/failure denominators and per-arm costs. Complete-case selection can bias
the result; no failures silently become zero recovery. Differences use exact sets,
not counts alone. Paired bootstrap uses 10,000 deterministic resamples of pass
clusters: connected overlapping intervals for the same NORAD across stations.
This does not mistake repeated sliding windows for independent evidence. Intervals
are unavailable below ten clusters. Relative bootstrap intervals are unavailable
if any resample has a zero baseline; defined and undefined resample counts are
reported rather than silently dropping zero-denominator draws. Pass independence
remains an assumption and intervals are exploratory,
not a scientific-significance/publication gate. Satellite/day generalization,
multi-station combining, protocol expansion, and external archive corroboration
are outside this benchmark implementation.
