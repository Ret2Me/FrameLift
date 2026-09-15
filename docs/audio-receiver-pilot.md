# Public SatNOGS audio feasibility pilot

This is an exploratory, positive-control pilot, separate from the frozen IQ
campaign. It is not a prospective or blind sample, a publication-readiness
claim, or evidence about every SatNOGS modulation.

## Signal contract

An OGG/WAV container does not identify the physical signal representation.
For the tested CANVAS recordings the declared hypothesis is already
FM-demodulated audio, supported by the SatNOGS FSK flowgraph:

https://gitlab.com/librespacefoundation/satnogs/satnogs-flowgraphs/-/blob/ac654732b48434a248e8a26f165315fa250d658d/generic/fsk.grc

The RF discriminator, audio resampling and DC blocking occur before OGG
encoding. The new `audio_receiver.decode_audio_pcm` therefore passes this
PCM through real filtering and the existing fractional-clock/protocol bank;
it does not apply a second FM discriminator. This is a native receiver entry
point, not a wrapper around gr-satellites.

The separately explicit `usb_real_passband` path constructs analytic audio
with a Hilbert transform and an explicit carrier offset. It cannot restore
original RF IQ or information lost before/during OGG compression. That path
and AFSK1200 have synthetic positive tests, but are not validated on real
public recordings by this two-CANVAS pilot.

## Reproduction

From the project root, use separate output roots for any new acquisition or
experiment. Existing results are intentionally not overwritten.

```sh
rtk proxy env PYTHONPATH=src .venv/bin/python scripts/ogg_positive_pilot.py \
  --root work/my-ogg-pilot \
  --observation-ids 14366383 14115025

rtk proxy env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/ogg_native_pilot.py \
  --input work/my-ogg-pilot/inputs/14366383/audio.wav \
  --output work/my-ogg-pilot/native/14366383-v1.json \
  --mode fsk --baud 9600
```

Acquisition stores public observation metadata, the original OGG, its decoded
float32 mono PCM at the original sample rate, unchanged reference PDU bytes,
and SHA-256 identities. It checks each returned observation ID. Failed API
queries or unavailable audio are not zero-frame results.

The native pilot scans fixed six-second windows every three seconds with a
32-phase, five-rate timing bank and 16 selected timing hypotheses per window.
Reference bytes are not passed to the decoder. All raw sample intervals are
visited; filtering/clock guards can still exclude boundary symbols, so this
does not promise complete recovery of every transmitted frame.

## Interpretation of counts

Count a complete AX.25 PDU once per observation, not once per search window,
repeat, or overlapping hypothesis. The native path preserves received FCS
and checks it independently with a bitwise implementation before removing
exactly those two bytes for comparison. AX.25 address/UI structure is also
validated. Independent review additionally uses a binascii-based CRC check.

The component baseline is gr-satellites 5.9.0 on the identical PCM file with
explicit FSK9600/G3RUH configuration. Its HDLC block checks and removes FCS
before KISS output; this pilot retains the KISS artifact and independently
checks PDU structure. It is not an exact recreation of the station's
historical software/settings.

Report these quantities separately:

- frames already present in the archived observation;
- native frames versus the existing decoder on identical audio;
- frames absent from the archive but found by either audio decoder;
- frames found only by the native method and absent from both alternatives;
- archive/baseline frames not reproduced by the native method.

The initially acquired ISS observation 14206235 is **not a valid positive
control**: its only uploaded object is `ffff`, two bytes rather than an AX.25
frame. It is preserved and excluded from the positive denominator. A further
bounded AFSK search found no suitable retained audio/reference pair; this is
not a claim that such recordings do not exist.

Six-second silence and seeded Gaussian-noise smoke controls are repeated
across runs. They are only two unique control inputs, not independent noise
exposure for every observation. They do not establish a useful false-alarm
rate bound. The much larger IQ control campaign has a different input and
must not be borrowed to claim that this new audio path is calibrated.

No decoded telemetry is uploaded to SatNOGS by the pilot.
