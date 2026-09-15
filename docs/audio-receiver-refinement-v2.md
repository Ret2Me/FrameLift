# OGG receiver refinement v2

This is an offline native receiver refinement, not an AI-trained model and
not a wrapper invoking gr-satellites. The public-audio experiment currently
validates CANVAS GMSK 9600 / AX.25 only. It does not establish real-world
coverage of AFSK, PSK, CCSDS, or every protocol supported elsewhere in the
project. The frozen IQ experiment is unchanged.

## Two independent changes

`telemetry_yield.fast_ax25.FastAx25ProtocolDecoder` vectorizes NRZI,
zero-register G3RUH descrambling, and HDLC flag search. It retains the
reference decoder's frame ordering, both starting levels, unstuffing,
length limits, received CRC check, and AX.25 UI structure validation.
Differential and end-to-end tests check exact bytes, not just counts.

`telemetry_yield.diverse_timing.DiverseTimingReceiver` keeps the original
global top 16 timing candidates as an exact prefix, then considers two
phase-separated representatives for each tested clock-rate error. Phase
separation is at least 0.20 of a symbol on the circular phase axis.
Candidates are selected from the existing eye-score ranking without
reference packet bytes. With five clock rates, at most 26 candidates are
decoded; the actual count is often 23 because candidates overlap.

The clock change fixes a demonstrated development case where globally
high eye scores concentrated on too few rate/phase settings. It does not
promise a gain on every recording: in the two new validation recordings,
global and diverse banks returned the same 85 PDU set.

## Reproduce a single offline run

Use a new output name; old plans, journals and completed results are not
overwritten. Both comparison methods must receive the same WAV bytes.

```sh
rtk proxy env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/ogg_refinement_pilot.py \
  --input /absolute/path/audio.wav \
  --output /absolute/path/new-result.json \
  --mode fsk --baud 9600 --decoder fast --bank diverse
```

The explicit input contract is already FM-demodulated mono PCM, not raw
RF IQ or arbitrary audio merely because it has a WAV extension. The
driver visits six-second windows with a three-second hop and retains
received FCS before independent validation and comparison. See
`docs/audio-receiver-pilot.md` for acquisition and representation limits.

`--bank global` is the fast, unchanged timing-policy ablation;
`--decoder reference --bank global` uses the scalar protocol implementation.
Do not change these arms after inspecting results in a new cohort.

## Verified findings and boundaries

- Development: 15 to 16 frames, 2,118 B after refinement. All original
  frames retained; the added 56-B frame was also found by the baseline.
- New recordings: 85 frames / 22,440 B versus 66 / 17,424 B from the
  component baseline on identical PCM. The 19 additional frames already
  existed in the archive. Two of 87 archived frames remain unrecovered.
- The development archive gains one 70-B frame from reprocessing, but
  the existing decoder also finds it. No frame in these four recordings
  is absent from both the archive and the baseline while found only by us.
- Exact development repeats retain all PDU/FCS/provenance and all 163
  journal rows. Independent reviewers replayed received FCS using a
  separate CRC implementation.
- Observed fast-global wall time was about 2.41 times shorter than the
  scalar implementation. Diverse search was about 1.8 times faster than
  the scalar implementation despite its larger bank. These measurements
  had concurrent host workloads and are not isolated CPU benchmarks.

Short synthetic controls are smoke checks, not proof of a low station
false-accept rate. Absence from an archive snapshot is not proof of a
new satellite message; source attribution and repeat decoding are
required before such a claim. Publication and production readiness are
not established. The next archive cohort follows
`reports/ogg-archive-week-preanalysis-v1.md`.

Detailed byte-level results and audit paths are retained in
`reports/public-satnogs-ogg-refinement-v2.json` and
`work/satnogs-ogg-refinement-20260907/verification/`.
