# Runtime repair and source-bound replay, 2026-09-11

This is a correctness/operability repair, not evidence of a new demodulation
algorithm, additional telemetry, or publication/deployment readiness. Historical
campaign services, binaries and observation results were not changed.

## Diagnosed failures

| Private observation | Historical failure | Repair |
| --- | --- | --- |
| 3928 | Native 7/127 windows rejected; innovation aborted at baseline window 73 | Preserve finite sparse residuals when the 90th-percentile scale vanishes |
| 3929 | Native 21/128 windows rejected; innovation aborted at baseline window 75 | Same normalization repair |
| 3208, 3412 | GR startup `KeyError: 'Codec2'` | Remove dangling auxiliary application-sink references from the offline selected profile |

All 28 failing native windows reproduced on regenerated WAVs whose SHA-256 and
byte counts exactly match the historical shared PCM16 files. These are not
silent windows: their raw absolute 90th percentile is at a PCM16 saturation rail.
The pre-existing constant-input check already handled wholly constant PCM. After
FIR/DC removal the percentile can vanish even though some residuals remain.

The repair leaves the arithmetic on the previously accepted `q90 > 1e-12` branch
unchanged. On the formerly failing branch, exactly zero centered residuals return
an empty frontend, while every finite nonzero residual is retained using its peak
as scale. Empty malformed input, nonfinite values, subtraction overflow and
nonfinite normalized output still fail; errors are not converted wholesale to
zero-frame success. Native window receipts now label failed, nonempty conditioned,
or empty/no-usable-symbol-variation frontends separately.

For GR, all demodulator/FEC/framing settings and the main deframer output are
retained. `additional_data` voice/image application sinks are removed alongside
the already removed transports. Codec2 voice is out of scope and is never counted
as recovered telemetry. This does not provide a missing non-AX.25 validator.

## Verification and replay

The focused current-source DSP/receiver harness passed **26/26** tests, including
existing numeric oracles, sparse residuals, sparse saturated plateaus, exact
ordinary normalization parity, empty input and nonfinite/overflow cases.
The final dispatcher passed **36/36** normal tests, with two optional integrations
excluded from that count. The missing-profile native/Dire-Wolf silence integration
was then explicitly run and passed **1/1**.

Versioned artifacts: `work/decoder-runtime-repair-20260911-v1/`.

- Repaired native and innovation runs for **3928 and 3929 both completed with
  zero validated frames**, instead of aborting. This is an operability improvement,
  not reception gain. All 28 formerly rejected frontends completed nonempty.
- Initial GR replays failed because the review-only replay helper used `.json`
  for its SatYAML filename; this installed GR entrypoint requires `.yml`. Those
  failed logs and the original helper source archive are retained. The production
  dispatcher already used `.yml`. Corrected GR replays are separately recorded in
  `gr-profile-extension-retry-v2/`. Both now complete: **57 unique main-output
  81-byte main-output KISS-stream chunks for 3208, 82 for 3412**. These are not
  telemetry packets: the next transport layer still needs reassembly, and this
  deframer does not reject packets using CRC or Reed-Solomon. The independent
  protocol audit found zero known telemetry structures after reassembly; a
  600-second white-noise control produced 48 comparable raw chunks. All remain
  unresolved and are not a Rust demodulator improvement. See
  `reports/taurus-validation-20260911-v1.md`. The final GR replay summary SHA-256 is
  `565560726517162dbf4da6b3c276540d735950b0c2e1bdcb469d2011c8826948`.
- The full public14967362 candidate regression completed **1596/1596 tasks**
  with the same **three frames** as the historical uninterrupted run. The
  independent comparison receipt passed: all typed task commits, frame FCS and
  actual audio identities were checked. Only task timing and intentionally
  changed executable/session identities were excluded from semantic equality.
  `public14967362-candidate-receipt.json` SHA-256 is
  `75db4a63f44778ed1f71b3ac41ffa6eeada209f46cf68e19bbfa4e7860ef26a5`.
- Parent-owned expanded positive/negative controls are separate evidence and not
  included in these small-test counts.

## Explicit child executable identity

The dispatcher now accepts independently paired overrides:

```text
--native-binary /absolute/path/to/telemetry-yield-rs
--native-sha256 <64 lowercase hex characters>
--innovation-binary /absolute/path/to/innovation_audio_probe
--innovation-sha256 <64 lowercase hex characters>
```

Both path and digest must be supplied together for each override. Relative paths,
malformed hashes, wrong bytes, missing plan bindings and swapped receiver bindings
fail closed. `polyitan-audio-plan-v3` names both child identities; each child is
checked before and after execution, and changing the plan prevents reuse of old
committed arm results. With neither override supplied, the documented historical
pinned defaults remain in force. Rebuilding the dispatcher alone therefore does
**not** implicitly adopt the repaired child executables.

Candidate native SHA-256:
`e59e8dac6991984de1b7b1e7238835dd3ad740150ae5004070c215290ee2fda3`.
Candidate innovation SHA-256:
`638066b9c3edee6227373aa7919c4ee8d9705ca4ad89426f30398987b6e5042a`.
Both are under the versioned artifact root's `bin/` directory.

The candidate dispatcher SHA-256 is
`3ffe71f94d81b47d8d484f50d5028d5fb00d186211b1a4f6f60bd978b5ed12c0`.
Its `dispatcher-routing-v3/plan.json` was generated with both explicit repaired
children in routing-only mode; no demodulation was launched by that preflight.
It intentionally omitted supplemental profiles, so its 252/538 routing coverage
must not be compared with the earlier 440/538 supplemented cohort.

## Independent historical resume regression

`work/mm-review-20260911-v1/public14967362-resume-receipt.json`, SHA-256
`8618fe701ad1fd236e1fac1a387b4e5a87e896fd157d03ee6def2a77283ac064`,
passed a separate historical-binary regression. Resumed and uninterrupted outputs
have identical full final JSON, three received-FCS-bearing frames, identical typed
session manifests and all 1596 task semantics, excluding only task elapsed time.
Every typed task commit digest, session/stage/window binding and received frame
FCS was independently checked. Actual input bytes were rehashed.

That comparison uses the same historical binary, policy and shared PCM16 source;
it is neither the newly repaired binary's qualification nor proof of a lossless
OGG representation. Timing differs with host load and was not treated as a speed
benchmark. M&M-specific review and gate findings are in
`reports/mm-review-20260911-v1.md`.
