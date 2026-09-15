# CANVAS #5122: independently reproduced positive IQ control

Audit date: 2026-09-11 UTC. This is a development diagnostic, not a held-out sensitivity benchmark or evidence of superiority of our receiver.

## Outcome

The station's archived 264-byte PDU is reproducible from its retained IQ. An existing source-compatible SatNOGS FSK chain recovered that PDU exactly. Feeding its hard decisions into our existing Rust AX.25/G3RUH deframer also recovers the exact PDU and the received FCS `03 ea`, independently verified by CRC-16/X-25.

Thus the failed local OGG attempts are **not explained by a missing CANVAS profile, nor by rejection of this valid PDU by our AX.25 parser**. The relevant gap is upstream of our deframer: waveform preparation, frontend filtering/frequency tracking, symbol synchronization, or their combination. The diagnosis does not yet separate all those causes.

The archived PDU contains an AX.25 UI envelope with a 248-byte CCSDS-space-packet-shaped information field. CCSDS here is inside AX.25, not an alternative physical modulation. The entire 248-byte information field must be retained; its last two bytes are not the outer AX.25 FCS.

**Follow-up:** our existing generic Rust receiver has now also recovered this exact PDU end-to-end from the full original IQ. This is no longer merely a successful Rust deframer test. The unresolved zero result concerns the retained OGG/audio path, not a blanket inability of our Rust receiver to decode the signal. See the follow-up section below.

## Controlled results

| Input / processing | Raw candidates | Strict AX.25 UI / exact archived match | Integrity evidence |
|---|---:|---:|---|
| Original OGG, frozen campaign's four arms | 0 in every arm | 0 | Existing campaign records |
| Downloaded IQ, gr-satellites raw-int16 interface | 0 | 0 | Exit 0, empty retained KISS; exploratory initial check |
| Same numeric float32 IQ, gr-satellites 5.9.0 | 1 | 0 / 0 | 103-byte PDU fails strict address structure; stripped FCS |
| Same numeric float32 IQ, source-compatible SatNOGS chain | 1 | 1 / 1 | CRC check enabled, correction disabled; exact archived bytes |
| Full original ci16 IQ, existing generic Rust receiver | 1 | 1 / 1 | Full received frame including valid `03ea`; channel-conditioned phase-FSK route |
| SatNOGS hard decisions, our existing Rust deframer | 1 G3RUH, 0 plain | 1 / 1 | Received FCS `03ea` retained and independently verified |
| Instrumented SatNOGS chain with extra soft-output sinks, Rust deframer | 1 G3RUH, 0 plain | 1 / 1 | Exact same PDU and valid received FCS |

Do not sum those repeated recoveries: **one unique PDU, 264 bytes, including 248 bytes of encapsulated information**, has been independently reproduced. This does not add a newly discovered packet to the station archive.

## Provenance and input contract

- Observation: private instance `https://polyitan.duckdns.org:8001`, ID 5122, CANVAS / NORAD 68635, station 41.
- Metadata start/end: `2026-09-09T22:47:11.292692Z` / `2026-09-09T22:54:22.668956Z`.
- Transmitter metadata: GMSK, 9600 baud, 437250000 Hz. Production client reports `gr-satnogs`, unspecified radio version, client `0+untagged.39.gef23d74.dirty`.
- Device sampling metadata is 200000 Hz. That is **not** the inferred saved-IQ rate.
- Station PDU URL: `https://polyitan.duckdns.org:8019/iq-data/data_obs/2026/9/9/22/5122/data_5122_2026-09-09T22-50-39`.
- Retained IQ URL: `https://polyitan.duckdns.org:8019/iq-data/observation_5122.iq`.
- HTTPS used the valid `polyitan.duckdns.org` certificate, pinned to `10.0.0.11`; no TLS bypass, upload, or telemetry submission.
- IQ download was bounded to 100 MiB and returned 98,397,764 bytes, matching S3 listing/HTTP metadata. The complete file was acquired, not a selected signal-containing crop.
- Interpreting it as little-endian interleaved signed-int16 I/Q gives 24,599,441 complex samples. At 57,600 complex samples/s that is 427.073628472 s, only 0.649 ms different from the original OGG duration, 427.072979167 s.
- The ci16/57600 interpretation is supported by this duration agreement, the historical SatNOGS IQ sink source, and successful exact-frame recovery. There is no per-file SigMF manifest or verified current-production dump configuration in this audit, so the rate and signal-chain location remain an evidence-supported interpretation, not a direct metadata assertion.
- The historical tag-1.5 flowgraph dumps post-Doppler IQ and converts complex floats to interleaved int16 using scale 16768. This audit converts stored int16 to float32 by division by 32768, exactly representable for every int16 component, preserving relative amplitudes without quantization or resampling. Both compared IQ receivers consume the resulting byte-identical float32 file. This common normalization is not a claim to reproduce original device amplitude.
- Original OGG is mono Vorbis, 48000 samples/s, with `ENCODER=libsndfile`. Its complex phase information has already been discarded. It is not interchangeable with IQ.

## Packet validation

Archived PDU fields:

- Destination `CANVAS-0`, source `LASP-0`, control `03`, PID `f0`.
- 264 bytes before the on-air AX.25 FCS; 248 bytes of information.
- Inner header `08 20 fc 69 00 f1`: CCSDS version 0, telemetry packet, secondary-header flag 1, APID 32, unsegmented sequence flags, sequence count 15465, packet-data length field 241. Total implied space-packet length is `6 + 241 + 1 = 248`, exactly the information length.
- This inner interpretation is structural, not validation of an undocumented inner checksum or semantic telemetry fields.
- Computing CRC-16/X-25 over all 264 archived bytes yields `ea03`; on air the received FCS is little-endian `03 ea`. The original Rust deframer finds that FCS in the received bitstream, not by appending it to a candidate for acceptance.
- Treating the archived last two bytes `a9 dd` as the outer FCS fails, as expected for an FCS-stripped PDU. The archival-only audit records that fact rather than silently dropping bytes.
- A separate reference-guided location diagnostic finds the complete stuffed frame at sliced-symbol index 1,943,612, nominally 202.459583 s at 9600 baud, spanning 2148 stuffed bits. This locator uses a known reference only for development localization; it is not blind recovery, a precise IQ sample index, or eligible independent test-set tuning.

The operator's historical CANVAS design report describes CCSDS packets inside UHF AX.25 frames, but does not establish the final flown inner-checksum convention: [CANVAS Fall 2021 overview, section 7.5.4](https://www.colorado.edu/aerospace/sites/default/files/attached-files/canvas_fall_2021_overview.pdf).

## Exact comparator and its limitations

The adapter is the pre-existing `work/ab-satnogs-offline/offline_satnogs_fsk.py`, copied unchanged into this audit directory. It uses SatNOGS flowgraphs tag 1.5, commit `c135a61558a61f3249737282c00095832bc1ba10`, and the source-exact gr-satnogs v2.3.4.0 AX.25 decoder harness, commit `8d94dc292ace327dcd15dba66dd2020031b2d5c7`.

The runtime is GNU Radio 3.10.9.2 in place of the upstream-declared 3.8. This is **a source-compatible comparator, not the verified current-production binary or a bit-exact historical runtime**. The local vendor checkout currently has flowgraphs tag 2.0; tag-1.5 `generic/fsk.grc` was independently checked with `git show` against the recorded hash and archived separately. Nothing in that vendor checkout was changed.

Frontend settings from the adapter:

1. 57.6 ksample/s complex input; relaxed Hamming FIR, cutoff 12000 Hz, transition 4800 Hz, 29 taps.
2. Quadrature AFC discriminator gain 1; moving average 1024, scale 1/1024; VCO sensitivity -57600, source delay 512, complex multiplication.
3. Channel Hamming FIR, cutoff 6000 Hz, transition 1200 Hz, 115 taps, decimation 3 to 19.2 ksample/s.
4. Quadrature discriminator gain 1.2; long-form DC blocker length 1024.
5. Mueller–Muller float clock recovery: omega 2, gain-omega `2*pi/100`, mu 0.5, gain-mu `0.5/8`, relative omega limit 0.01.
6. Binary slicing; AX.25/G3RUH decoding with CRC checking, no error correction, promiscuous addressing, maximum frame length 1024.

Two executions, one with extra diagnostic sinks, have different hard-decision lengths and hashes (4,100,282 vs 4,100,312 decisions), although both recover the same exact packet. Additional sinks can alter scheduling/numerical boundaries/EOF handling; the precise source of the variation was not isolated. Do not claim bitwise DSP determinism from these runs.

## Stage-isolation exports

All files are under `work/canvas-reference-audit-20260911/`:

- `satnogs-before-clock.f32`: continuous little-endian float32 after discriminator and DC blocker, **19200 samples/s**, 8,199,813 samples.
- `satnogs-before-clock.wav`: identical sample values wrapped as a float32 mono WAV; no scaling/resampling.
- `satnogs-after-clock.f32`: continuous float32 clock decisions, nominally **9600 symbols/s**; not a uniformly timed original recording.
- `soft-export-sliced.u8`: hard decisions from the same instrumented graph.
- `soft-export-rust-deframer.json`: independent proof that the instrumented graph still contains the archived frame with valid received FCS.

The Rust-owned diagnostic executable invokes the installed GNU Radio adapter for that external comparator only. It does not replace our receiver with Python and does not modify existing receiver code.

## Artifact identities

| Artifact | SHA-256 |
|---|---|
| Frozen original metadata | `189483e046de460c95854a48e7f3859af2c372940c2e61f95f3d34d0804cfdbd` |
| Original OGG | `96c6834a7b534fbaed52297dca821178a3f2ad42054ccf68893c66b09949b034` |
| Station 264-byte PDU | `795faee594e744d9d67a2c2117dfe08f4ab17be31cba5f4d55629bad5babf18e` |
| Original IQ ci16 | `82ba896e59b4dedab776a1d11087facd5cd9b0944319b430e0143ee38881ecf7` |
| Shared IQ cf32 | `0ea2be5abe092829a7477f556620f73941c60f119b1dc8529b7737ba6dca41b3` |
| Frozen CANVAS profile | `6ca571b55b7858ccfcd0fd679b9756946edc606593a9e30524f9272a0ad47a43` |
| Source-compatible adapter | `7e9c99ebc81786d49fa1372fc2abf0ea5a53db68d5d2edb52de4c40a1a433a5d` |
| gr-satnogs decoder executable | `cd0c0eb8f74a53e44f7dbc1bb562be09d4649bb908b49d090e514f2f746558e2` |
| Tag-1.5 `generic/fsk.grc` | `dda3c64161bdffacbf516a86e81e87fce6294815ec22c24770e747d0b9f38032` |
| Tag-1.5 flowgraph archive | `798e999d83277ff9daed7ec06513fda5ecd831c1ce9f5861d92f24fed4a0b005` |
| SatNOGS comparator result | `afc9e71f878ce6c09b062ade598b497a45f23483699fa55e0022bbe927fd94d9` |
| Independent Rust deframer result | `108b74d2f44e6589d0eb434332742f51eae1cc9c8be704d49bc9784a57b6ac19` |
| Shared-cf32 gr-satellites process result | `27a1fe5231fc497df557b6a5671e953af2bb385868ed54badc57b80fa1872586` |
| Before-clock float32 WAV | `f663e44ba7f8a0f008bf8b556dd707031c2c55dfbc57492730b63aea4fec1116` |
| Frozen diagnostic Rust source v1, work-directory `canvas_reference_probe.rs` | `e5d3efa23fe49c71aad62c171826b157f8c8f2680840dc03dc47f93f7dc71a57` |
| Diagnostic executable v1 | `b6d8ffbb137cf01acb99e4bf9e668f71a35aed5b6692634588692141b2590698` |

Process details, source hashes, full received frame, original HTTP headers and bounded S3 listings are retained in the audit work directory. The `gr_satellites` entrypoint hash alone is not a full dependency-closure hash; the version is recorded and the complete production software environment is not frozen by this audit.

## Verification and next decision

- Diagnostic CRC known-vector test and single-bit corruption rejection test: 2/2 passed.
- Real-data integration checks: archived PDU structure, exact IQ-reference reproduction, independent Rust received-FCS verification, instrumented-frontend recovery, and strict rejection of the 103-byte gr-satellites candidate.
- Observation #5169 has no `demoddata` reference in the frozen metadata. Its matching IQ object exists (94,640,068 bytes), but was not downloaded in this bounded audit; it is not a positive control merely because the native path ran.
- No old campaign result, production service, profile, or decoder implementation was modified.

Next: compare our full receiver on the before-clock float32 export, then on the same shared IQ. If it succeeds on exported discriminator audio but fails on IQ, focus on frequency tracking and frontend filtering; if it fails on that audio but succeeds on the exported hard decisions, focus on clock recovery and amplitude normalization. Separately inspect original OGG versus lossless exported discriminator audio to quantify the loss introduced by the audio branch/codec/preparation.

Until positive controls like this are reproduced by the intended end-to-end receiver, the current private-cohort zero-result campaign is not a sufficient basis for final performance claims or an improvement paper.

## Follow-up: OGG representation and aligned audio branch

### Positive end-to-end IQ control

`work/decoder-readiness-20260911/canvas5122-generic-iq/result.json` independently records one frame from our existing generic receiver on the complete original ci16 IQ. Its frame bytes match the station PDU plus received FCS `03ea`. Result SHA-256 is `c064abbf3600c6fadcbebec8f796128d6125f1b52d85e02aa0ddc3477239e49b`. This was produced by the parent task, not by changing the receiver in this diagnostic subtask.

### Verified historical branch topology

The actual tag-1.5 source is now extracted at `work/canvas-reference-audit-20260911/source-tag1.5/generic/fsk.grc`, SHA-256 `dda3c64161bdffacbf516a86e81e87fce6294815ec22c24770e747d0b9f38032`.

After the same AFC and 6000 Hz channel filter, the tag-1.5 graph splits:

- Decoder: quadrature gain **1.2**, 19200 samples/s → long DC blocker 1024 → M&M clock recovery → slicer.
- Audio: quadrature gain **0.9**, 19200 samples/s → PFB resampling by 2.5 to 48000 samples/s → long DC blocker 1024 → Vorbis quality 1.0.

The **0.9** gain is verified in tag 1.5. Gain **1/pi** belongs to the current local tag-2.0 checkout; do not conflate those revisions. The initial informal follow-up message used the latter gain, and was corrected before the tag-1.5 audio export was run.

The original file has encoder comment `libsndfile`, whereas the archived gr-satnogs custom encoder source writes `satnogs ogg encoder`. This is concrete evidence against claiming the original audio was encoded by that historical binary. It does not by itself identify the current production implementation.

### USB/passband hypothesis tested and rejected for a 12 kHz carrier

The original OGG was decoded without clipping into the parent task's float WAV, SHA-256 `6f15a822e47bf8fb551a4ecc2db41e20a81dfb0c10c821ae665ff4319df08904`.

Our bounded spectral probe uses 100 uniformly selected Hann-windowed 8192-sample FFTs. It is an orientation diagnostic, not a calibrated RF signal-power measurement.

| Positive-frequency power fraction | 200–206 s around known frame | Across 0–427 s |
|---|---:|---:|
| 0–6 kHz | 0.778669623 | 0.777998936 |
| 6–10 kHz | 0.220940313 | 0.221596929 |
| 10–12 kHz | 0.000390064 | 0.000404135 |
| 12–24 kHz | 3.46e-13 | 1.99e-12 |

There is no evidence of a passband centered around 12 kHz. Combined with the waveform alignment below, this supports a demodulated baseband audio contract for this recording. It does not automatically establish that contract for every satellite or every recording in the cohort.

Artifacts: `ogg-spectrum-200-206.json`, `ogg-spectrum-full.json`, and comparison `iq-preclock-spectrum-200-206.json` in this audit work directory.

### Lossless reconstruction and alignment

The diagnostic v4 wraps the unchanged source-compatible adapter, captures its channel-filter output, and adds the verified tag-1.5 audio branch **without Vorbis encoding**. `historical-ogg-lossless.wav` is float32 mono 48000 Hz, SHA-256 `3c4794c4cd778ce1b74dd7972a9d32a5e4c8435e5dd024f11621b1fe87728803`.

The original clock/slicer path still independently recovers the exact archived PDU in this instrumented run (`historical-ogg-export-rust-deframer.json`). The parent task's new Rust M&M probe also recovers the PDU from the complete lossless 48 kHz audio in all 24 fixed branches (`work/decoder-readiness-20260911/mm-historical-lossless/result.json`).

We compared 0.25-second templates of this lossless reconstruction against original OGG audio in broad 25–30 second neighborhoods. FFT-based normalized cross-correlation finds:

| Template start | Best original-OGG start | Pearson correlation | Best scalar original / historical |
|---|---|---:|---:|
| 100 s | 100 s | 0.942839819 | 0.319287459 |
| 202.4 s | 202.4 s | 0.959939550 | 0.326148311 |
| 300 s | 300 s | 0.937055262 | 0.316153457 |

Every best offset is **zero samples** at 48000 Hz. A same-file sanity control gives correlation 0.999999999999985 and exactly the expected offset. This rules out a gross time shift or an unrelated/wrong recording as an explanation for these windows, and is strong evidence that the retained OGG contains a representation of the expected discriminator audio, not USB audio awaiting another FM discriminator.

The residual variance after a single best gain is approximately 7.85% at the packet window, and 11.1–12.2% at the other two windows. That residual combines encoding, filtering/gain conventions, stored-IQ quantization, and numerical/AFC differences. Correlation alone does **not** prove Vorbis caused the failed frame.

### Bounded controlled codec roundtrips prepared

To separate amplitude scaling from encoding, the lossless historical audio was rescaled by `1/(0.9*pi) = 0.353677651315323` into `historical-ogg-scaled-float.wav`. Separate FFmpeg/libvorbis roundtrips then used q3 and q4, retaining float32 decoded output:

- q3: nominal 80000 bit/s, actual file 2,948,756 bytes.
- q4: nominal 86000 bit/s, actual file 2,966,802 bytes.
- Original OGG: nominal 86000 bit/s, file 2,974,070 bytes.

Both derived recordings are controlled transformations of real IQ, **not original station recordings or exact production-encoder reproductions**. q4 was prepared because its nominal rate matches the original; no further codec variants were added.

At 202.4 s, scaled lossless versus q3 roundtrip correlation is 0.959969025, with best gain 0.922913504; original OGG versus that q3 reconstruction has correlation 0.989313571 and zero lag. This demonstrates a closely comparable local distortion in a controlled Vorbis roundtrip. The matched decoder checks below separate gain from encoding.

| Follow-up artifact | SHA-256 |
|---|---|
| v4 source (`canvas_reference_probe_v4.rs`, also current example at this checkpoint) | `a77ba7890d52f610bf03930a99603ff42dd0db06d5fad1ec405e48bfcbf98e54` |
| v4 diagnostic executable | `31d7fce215329d651a9a188b7564e22c236621d38339223b97e31e6405127aa6` |
| Scaled lossless float WAV | `c965b2f61d3c15cb02630f972bad0a1f45684b816ee35ffdfcb4c7d3f35e56d3` |
| Controlled q3 OGG | `47438223a35f530ea6fb2365bcc8c1ffab003bd5304962340dfc73e4f3d4be7f` |
| Controlled q3 decoded float WAV | `98833bb988064a625fd702cb226f4041dd6c6dd607fa3a34b6cfd50a5fa50291` |
| Controlled q4 OGG | `e5c6cbd89629021f41b25570fb996e1d318a36b78e197256a92e2a685bd86cc7` |
| Controlled q4 decoded float WAV | `c91c32d328c2fdc2b203cac88faffbae0db4a142b0849bad65bd803004ada211` |
| Original-OGG vs lossless correlation at 202.4 s | `c4611f1032f38d33118b52dd2ea6068463277d491e492d34891effec29015102` |

These are selected development controls. One recovered reference frame, even now reproducible end-to-end on IQ, does not establish general performance or scientific novelty. No production setting or frozen campaign result was changed by this follow-up.

### Completed matched-gain codec ablation

The same frozen Rust M&M v2 executable (`9713d2fa98d5d264f4ecaa3158aa8330ef2ac2388794863434b29433c6822d12`) and fixed 24-branch bank were run by the parent task on the complete recordings:

| Input | Exact reference frames | Successful branches / completed branches |
|---|---:|---:|
| Historical lossless audio, discriminator gain 0.9 | 1 | 24/24 |
| Scaled lossless audio | 1 | 8/24 |
| Same scaled audio after controlled Vorbis q3 roundtrip | 0 | 0/24 |
| Same scaled audio after controlled Vorbis q4 roundtrip | 0 | 0/24 |
| Original station OGG, float-decoded | 0 | 0/24 |

All eight successful scaled-lossless branches use input gain 1.0, spanning both interpolations and all four initial phases. Input gains 0.5 and 2.0 fail there, so clock behavior is amplitude-sensitive; scaling is not innocuous for every branch. Nevertheless the **same scaled signal before versus after encoding** changes the fixed-bank union from one exact frame to zero, without a change in settings.

**Bounded causal conclusion:** these controlled q3 and q4 codec roundtrips are sufficient to break this fixed receiver's recovery of this particular packet. This is not proof of universal or irreversible information loss, and does not show that every alternative decoder must fail. The original production encoder, its full configuration, and every other frontend difference were not reproduced bit-for-bit.

Result hashes: `mm-historical-lossless/result.json` `b46230c96419ebc3e547cb91ad1ba9c74d8b8a5899d3f7a038f7d6fa37a2bad0`; `mm-scaled-lossless/result.json` `90545ae88338654d39345bc1a37e7eb925beb1f944733dce9a3e4a4fbcde9896`; `mm-controlled-q3/result.json` `9a75d592694be70e327cbad4a11d22accd913664a1f576c36e4edd09a29861f5`; `mm-controlled-q4/result.json` `9d3b1227c93d4db2bfb4057b9601f7ca971da08ecc12354ce2bb4c7916599544`. These files are under `work/decoder-readiness-20260911/`.

### Post-hoc bit-level diagnosis, separate from decoding

Diagnostic v5 runs one fixed M&M branch (linear interpolation, initial phase 0, gain 1) on the **entire** input and performs ordinary blind AX.25 decoding before loading the reference packet. Only afterwards does it compare the resulting NRZI-decoded/G3RUH-descrambled stuffed-bit sequence with a re-encoded reference within a fixed nominal 200–205 second neighborhood.

| Input | Blind exact matches | Minimum integer-aligned mismatches / 2148 stuffed bits |
|---|---:|---:|
| Scaled lossless | 1 | 0 |
| Controlled q3 roundtrip | 0 | 122 |
| Original station OGG | 0 | 78 |

The mismatches include clustered pairs offset by 12 and 17 bits, consistent with propagation through NRZI/G3RUH transformations. These counts are **not raw RF BER**, do not account for insertion/deletion alignment, and are not an error-corrected decode. No reference bytes influenced timing, search settings, CRC acceptance, or reported receiver yield.

Frozen v5 source SHA-256: `6db642067a2fd719a4f05312eec7ed364fb5d9301f5c42a13a40c5f0d2e2bed9`; executable: `64aa05a70e407eae202024556119dc8d1ad85354c1ea9df3b9085be8e9afb517`. Included M&M source: `e11877bbaaa799af1c39c0111b97e243a646d07b3fcbaa03d462c8a616e48547`, copied unchanged as `mm_clock-v5-source.rs`. The v5 source snapshot is `canvas_reference_probe_v5.rs`. Seven embedded tests pass, including five M&M arithmetic/guard tests and two CRC tests.

Post-hoc result hashes: `posthoc-scaled-lossless-bits.json` `1806b6d0cc05ca3f1aa8a0eaca66f1eec57e165d55aa1dd94e39f404dd9cc8c1`; `posthoc-q3-bits.json` `4dfe7ba9d8abff27b1850dbc3586d7688b082c54bfbc4ce1af4a578189f9d097`; `posthoc-original-ogg-bits.json` `7f1d60fb39d1e184da79aa9ec9cbf99b76964f9329a14cc70ccceddc74a6261e`.

## Independently audited additional packet on lossless IQ-derived audio

The existing frozen progressive receiver completed all 994 tasks on `historical-ogg-scaled-float.wav` and returned **two unique received-FCS-valid AX.25 UI frames**, one matching the station archive and one additional PDU. This is a development result on a lossless reconstruction from real observation #5122 IQ. It is **not an additional recovery from the original OGG archive**, a held-out result, an equal-compute benchmark, or evidence of population-wide doubling.

Diagnostic v6 independently recomputed CRC16-X25 with a separate bitwise implementation rather than calling the decoder's table implementation. It checked strict AX.25 UI structure and CCSDS primary-header consistency, then verified the exact hashes of all **994 task commits**, unique stage/window keys, the task-union/summary agreement, the session-to-manifest binding, and the actual audio-file hash. Every check passed. The verifier reads already-produced results; it does not run a decoder or supply expected bytes to a receiver.

| Packet | Archived exact match | PDU bytes / information bytes | Received FCS, little-endian | CCSDS APID / sequence | PDU SHA-256 |
|---|---|---:|---|---|---|
| Additional | No | 264 / 248 | `05 65` (computed CRC `6505`) | 32 / 15460 | `dbe14147d44f34488eefe3ed8d892392996b5e03b08502d569345d4600867360` |
| Existing reference | Yes | 264 / 248 | `03 ea` (computed CRC `ea03`) | 32 / 15465 | `795faee594e744d9d67a2c2117dfe08f4ab17be31cba5f4d55629bad5babf18e` |

Both have destination CANVAS-0, source LASP-0, UI control and PID `f0`. Both inner primary headers describe version-0 telemetry packets with a secondary-header flag, unsegmented sequence flags, and a data-length field of 241, exactly consistent with 248 total inner bytes. **No undocumented inner checksum is claimed valid.** The extra PDU differs from the archive in 45 byte positions. Its full 266-byte frame including the received FCS hashes to `c09d7736655220b2ff5b960d10e99702425878ec2ba1830bfe273491b3ed0d59`.

### Where the extra packet comes from

- The additional frame appears only in target window 64, covering **192–198 seconds** in the IQ-derived 48 kHz audio. Both `early-nearest` and `nearest` stages recover the same packet, each in 14 parameter branches; these are correlated repeated detections of **one** physical packet, not 28 independent frames.
- Every successful extra-frame branch uses `window-67/legacy-fir512` as the channel anchor. That anchor covers **201–207 seconds**, separated from the target by a 3-second gap, exceeding the receiver's 1-second disjoint-window guard.
- The anchor's training span is symbols 12667–14815 and identifies the already-received archival frame by its full-frame hash `32edd26fcb0024a6718a5a402268c42b7b10fd011f4725bacb72f51f8c9b41c3`. Guarded training uses 2108 received symbols. Quick and complete-baseline generations have separate fitted models despite the shared anchor ID.
- Code inspection confirms the baseline's own received frame is CRC-checked before its waveform span is used to fit a three-tap channel model. The transferred model then drives a four-state sequence detector on the **target waveform**; no archive packet bytes, target-packet contents, CRC-guided list search, or bit repair are supplied to that detector. The target frame is independently checked after detection.
- Relevant paths are `rust/adaptive.rs` (`checked_spans`, `first_window_partition`, `select_anchor`, `supplemental_window`) and `rust/sequence.rs` (`fit_channel`, `detect_sequence`). The mechanism uses established sequence-estimation components; this audit does not establish novelty.

The native generic-IQ receiver and the source-compatible SatNOGS comparator each recovered only the archival PDU in the completed full-IQ runs documented above. Their input frontend/search budget differs from the progressive reconstruction experiment. Relative to the one-PDU station archive, this selected observation contributes **one additional unique 264-byte AX.25 PDU containing 248 bytes of inner packet data**. Independent recovery of that additional packet by another receiver has not yet been shown.

### Exact input and artifact chain

The actual file hashes were rechecked after decoding:

1. Private S3 `observation_5122.iq`, SHA `82ba896e59b4dedab776a1d11087facd5cd9b0944319b430e0143ee38881ecf7`, interpreted as little-endian complex int16 at inferred 57600 samples/s; no fresh RF acquisition or synthetic packet insertion.
2. Componentwise float conversion by `/32768`, `observation_5122.cf32`, SHA `0ea2be5abe092829a7477f556620f73941c60f119b1dc8529b7737ba6dca41b3`.
3. Pinned source-compatible SatNOGS frontend plus the verified tag-1.5 pre-Vorbis audio branch, `historical-ogg-lossless.wav`, SHA `3c4794c4cd778ce1b74dd7972a9d32a5e4c8435e5dd024f11621b1fe87728803`.
4. Fixed scalar `1/(0.9*pi)` applied to that lossless waveform, `historical-ogg-scaled-float.wav`, SHA `c965b2f61d3c15cb02630f972bad0a1f45684b816ee35ffdfcb4c7d3f35e56d3`. It is the directly consumed source and prepared WAV in the progressive manifest; no further conversion is recorded.
5. Frozen progressive executable `work/progressive-20260910-v3/bin/telemetry-yield-rs`, independently rehashed as `633f1f4ecaf7d4617f46fda83bf0866223bc123fa5013e3146cc3e32edefbb91`, matching the manifest.

The earlier zero-sample-lag waveform comparisons support the association with the original station OGG, but do not establish a bit-exact production frontend or encoder. The IQ format/rate remain a well-supported interpretation rather than explicit per-file SigMF metadata.

| Audit artifact | SHA-256 |
|---|---|
| Progressive result `work/decoder-readiness-20260911/full-scaled-lossless/result.json` | `d69eadcdcc03d43672682f34f585087fa823ce4f59a8a574c819e90196776df4` |
| Progressive manifest/session | `be342d9396719fe8301713af632581e7d854ac3336560044d275d0b5a25300c2` |
| Independent `progressive-two-frame-audit.json` | `8bf5d204750f2faa29fca6ce6cb6f0e396a03a43eedc14a932927c588e0597c2` |
| Diagnostic v6 source | `2ce1aeff30e761970e44e8e433c67a60a755fc121468db3140ac7d3f50a824c0` |
| Diagnostic v6 executable | `5c03e2a975aed501ae0cd8043d500c60b149ad08cf9b00d18e2a76ddd7178e5c` |

Nine diagnostic tests pass, including an independent standard CRC vector and a task-commit mutation rejection. The source/executable and JSON audit are retained under `work/canvas-reference-audit-20260911/`.

The parent also completed the same 994-task bank on the controlled q3 roundtrip and obtained zero frames. Together with the fixed M&M codec controls, this strengthens the bounded finding that these lossy audio transformations defeat the receivers tried on this observation. It does not prove that no future OGG receiver can recover them. A publication still needs a frozen evaluation, independent observations, compute-matched baselines, and false-positive controls; this single development case is not enough.

## Matched-clock ablation: sequence memory versus a plain slicer

Diagnostic v7 replays **every one of the 48 recorded clock/gain trials** in the extra packet's target window for each of the quick and complete-baseline anchor generations: **96 paired trials**, with no tuning or selection of only successful branches. The target waveform, legacy frontend, sampled soft-symbol vector, learned aggregate channel model, gain, and protocol acceptance are shared by the compared arms.

For each trial the comparisons are:

1. Plain hard slicing at `gain * learned_model.bias`, so the slicer receives the same model's gain-adjusted decision center.
2. An additional plain-slicer control at the original recorded timing hypothesis's threshold.
3. The existing four-state sequence detector with the recorded channel model and gain, followed by the same received-FCS/UI validation.

Only after all arms finish does the diagnostic compare the re-run sequence detector's frame sets with the historical trial outputs. It fails closed on any discrepancy. **All 96 sequence-detector frame sets reproduce the frozen trial results exactly.** The output union equals the independently audited extra packet byte-for-byte.

| Anchor generation | Complete paired trials | Model-bias slicer successes | Recorded-threshold slicer successes | Sequence-detector successes |
|---|---:|---:|---:|---:|
| Quick anchor | 48 | 0 | 0 | 14 |
| Complete-baseline anchor | 48 | 0 | 0 | 14 |
| Unique target packets, both generations combined | — | 0 | 0 | 1 |

This rules out **only reusing those clock hypotheses or shifting the slicer to the learned bias** as an explanation for this selected extra packet. Sequence estimation using the learned neighboring-symbol channel terms contributes beyond those matched plain-slicer controls here. It does not establish that all possible slicer thresholds fail, that this algorithm is globally optimal, that compute costs are equal, or that the result generalizes to other observations. The target window and model generations were selected after observing the original gain, making this a mechanism ablation on development data, not a held-out performance estimate.

No expected target bytes are used for recovery or acceptance. The source and all four underlying task commits are hash-checked, the target is exactly samples 9,216,000–9,504,000 (192–198 seconds), and the transferred anchor is disjoint at 201–207 seconds. The report records a SHA-256 over each actual sampled f64 soft-symbol vector. The complete-baseline model follows the original merge rule and has strictly smaller normalized MSE than the quick candidate; no ambiguous tie-breaking was inferred.

Artifact: `work/canvas-reference-audit-20260911/matched-slicer-ablation.json`, SHA-256 `559bc473f453feeade3169482af5abad5bc53615ddad32878c037a5d8fe4991d`. Frozen diagnostic v7 source: `0020fab7d39543e0f618a8dc8c86f7eec891737d711daf01941cf518400e7ded`; executable: `864e9f5f7ad34b5728daca9e14b6e208b95c1c577f48917e0ec45796951581c7`. The nine shared diagnostic unit tests still pass; the 96-trial exact replay is the integration check for this new ablation.

The linked retained Rust library has SHA-256 `97acc8f61715c4d5b967a77062586de62b15b4da5ee0c5ae653b9fdc5da8b0c9`. The frozen progressive source archive is SHA-256 `9d682bbf808127580d0c5484a637e0305c292968985ec2373d73217e052faaa5`. Its sequence code is identical to the inspected source; adaptive code differs only in visibility of four helpers, and later DSP differences are the separately owned sparse/constant-input repair. The exact frame-set replay establishes case-level compatibility, not universal binary equivalence.

The separate read-only windowed M&M code review is retained in `work/canvas-reference-audit-20260911/mm-windowed-independent-review.md`, SHA-256 `e80195b69eca8f7656844bf778f46ed4d9b9ac3536cb1ea055aa440b936830c2`. No practical fail-closed or loaded-source-binding blocker was found; a helper-only empty-layout guard was added defensively by the parent, with no claim of a previously reachable campaign bug.
