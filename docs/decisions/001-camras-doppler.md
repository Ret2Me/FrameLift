# ADR 001: CAMRAS Doppler state

Status: **accepted for ingest policy; archive-wide signal state remains mixed**
(2026-08-31; sample-rate correction accepted 2026-09-01).

## Correction: direct flowgraph evidence supersedes duration fitting

The initial pilot inferred 56 kS/s for observation 12511021 by fitting the raw
sample count to the SatNOGS API observation window. That inference was wrong.
The historically compatible SatNOGS `generic/fsk.grc` candidate at tag 1.5 and
the later tag 2.0 both derive a decimation of 6 for 9,600 baud and dump
post-Doppler complex int16 samples at **57,600 samples/s** before the downstream
FSK receiver. This pipeline evidence supersedes the timing-only estimate, but
the exact flowgraph revision installed for the observation was not serialized;
57.6 kS/s therefore remains an evidence-backed historical inference rather
than an exact C0 field.

At 57.6 kS/s, the stored file spans 551.5319 seconds, 16.4681 seconds less than
the 568-second API window. Therefore the API window is not a stored-IQ duration
contract; capture edges may be omitted. The earlier pilot report is preserved
as historical evidence. `reports/camras-replay-followup.json` records the first
reconstruction, and `reports/historical-c0-reconstruction-followup.json`
corrects its exact-tag claim. Tag 1.5 uses IQ scale 16,768, whereas tag 2.0 uses
16,384; a dedicated sensitivity replay produced the same complete-frame result
at both scales and rejects this difference as causal.

## Evidence

The bounded real-IQ pilot in `reports/camras-real-iq-pilot.json` found two
different capture regimes:

- CAMRAS observation 9614528 is consistent with 48 kHz and **pre-correction**
  IQ. Its CW ridge follows the historical TLE across about 18.16 kHz. The best
  model has inverted spectral sign, a 22.5 s raw-start offset, and p90
  ridge/noise 131.58, versus 7.85 for the opposite sign.
- Observation 12511021 contains 31,768,235 complex samples. The initial pilot
  fitted this to 56 kS/s, but direct flowgraph evidence later corrected the
  rate to 57.6 kS/s. Its target energy is nearly stationary: the best stationary ridge
  has p90 ridge/noise 1363.36, over 50 times the best affine TLE track. The
  demoddata times at +113, +114, and +352 s map under the 6/7 time scale to
  detected raw bursts at 130-138 s and 409-412 s.

Both files are severely clipped (about 72% and 75% of sampled scalar
components at an int16 endpoint). Gate A0.5 section 6.4 remains failed because
no local decoder output matched SatNOGS `demoddata` byte-for-byte.

### Headerless-file contract audit

A bounded audit used CAMRAS `HEAD` responses for 20 deliberately stratified
index rows (2019--2026; good, bad, unknown, and missing status; small through
large files) and attempted the corresponding SatNOGS metadata reads. It made no
IQ body requests and used concurrency one. All 20 `HEAD` requests succeeded.
The live SatNOGS endpoint returned HTTP 500 or timed out, so only seven
pre-existing, content-hashed API cache entries could be joined; the other 13
remain explicitly missing. The complete evidence is in
`work/camras/contract-sample.json`.

Those seven joined rows reject an archive-wide output rate. The original
timing sensitivity analysis classified four rows near 48 kS/s, two near
56 kS/s, and one near neither under a loose 30-second edge margin. The direct
57.6 kS/s reconstruction for 12511021 proves that such duration fitting can
misclassify a file when the stored IQ omits part of the API window. These fits
are retained only as review signals, never as accepted rate evidence.

For both 9614528 and 12511021, `client_metadata` names `gr-satnogs`
`v2.3-compat-xxx-v2.3.4.0`, enables IQ dump, points to
`/data2/camrasdemo/satnogs/iq/384.raw`, and names UDP port 57356. Its
`samp-rate-rx=1000000` is the SDR/front-end input rate, not the archived output
rate: the same value accompanies files with different timing signatures,
while another joined row has `samp-rate-rx=2048000`.
No post-channelizer dump rate or exact flowgraph identity is present. Likewise,
`doppler-correction-per-sec=null` does not identify the archived Doppler state.
The official SatNOGS flowgraph documentation also states that streamed complex
int16 sample rates vary by flowgraph and often by baud rate, but this metadata
does not prove that a CAMRAS file is that UDP stream.

## Decision

CAMRAS sample rate and Doppler state are mandatory **per-recording** metadata.
The ingest path must not default the archive to either 48 kHz or 57.6 kHz, and it
must not default recordings to post-correction. Each accepted recording needs:

- `sample_rate_hz` plus provenance or empirical evidence;
- `iq_doppler_state` in `pre_correction`, `post_correction`, or `unknown`;
- the spectral sign/component-order convention;
- any affine mapping between raw sample time and API observation time.

Timing-only inference may nominate a rate for manual review but cannot override
a known capture-pipeline rate or establish a stored-IQ duration contract. The
inferred rate, API duration, sample count, every candidate duration residual,
and margin must all be retained.
Front-end `samp-rate-rx`, rounded index size, or a best-fit quotient alone must
never populate `sample_rate_hz`.

When any field is unresolved, ingest remains fail-closed and Doppler-dependent
decoding or claims are blocked. Existing SigMF conversion must receive an
explicitly verified rate rather than silently applying the CAMRAS-wide 48 kHz
constant.

## Consequences

The 9614528 result permits residual-free, from-scratch Doppler experiments for
that recording only. The 12511021 result permits bounded post-correction
experiments at the candidate-flowgraph-derived 57.6 kS/s rate, but not
end-to-end or exact-C0 claims: the byte-identical replay still fails. A bounded
250-attempt M&M sweep and a deterministic all-hypothesis clock-bank replay on
the corrected, reference-informed event windows both recovered zero complete
CRC-valid reference frames. The deterministic runs repeat byte-identically,
which excludes scheduler nondeterminism in that substitute path but leaves
front-end conditioning and hard-symbol decisions unresolved. The archive needs
a per-file validation pass before a campaign can start.
