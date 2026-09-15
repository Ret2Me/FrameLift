# Generic telemetry recovery campaign v3

Date: 2026-09-02

## Outcome

This iteration produced one independently audited physical-layer improvement,
one useful modulation-routing baseline, one new real-IQ waveform plugin, and
one clear negative acquisition result. It does not establish universal
superiority over SatNOGS. The strongest frame-level result remains the earlier
same-IQ CANVAS result: ten trusted native AX.25+CCSDS payloads versus six from
the source-compatible baseline.

## Carrier/timing v2

The new opt-in RML24 path replaces the weak BPSK carrier estimate with a
bounded second-power FFT estimator, uses the corresponding fourth-power
estimate for QPSK/OQPSK, and emits six fixed OQPSK boundary hypotheses before
truth is available. GMSK remains bit-for-bit legacy as a control.

On the preregistered record-level +20 dB holdout, BPSK/QPSK/OQPSK BER fell from
`0.235669` to `0.149167` across 36 records and 34,820 truth bits. The paired
bootstrap 95% interval for the v2-minus-legacy delta is
`[-0.136965, -0.042036]`. BPSK improved by `0.235928` absolute BER and OQPSK by
`0.098291`; QPSK was unchanged. The wrong-record control remained at
`0.461200`. An independent audit regenerated all 144 declared holdout records
and all 432 legacy/v2/wrong-record comparisons with no discrepancy.

The full versioned replay scanned all 1,323,000 records and scored all 252,000
currently supported records. With the historical truth-aided alignment bound
of +/-8 bits, BER changed from `0.4637071743` to `0.4626663020`, or 228,332
fewer errors among 219,366,000 bits. This small full-run change is not a clean
receiver-gain estimate: the larger OQPSK hypothesis bank improves the oracle
minimum even at -20 dB, while the known +/-8 alignment limit still hides real
correlation. The preregistered wide-alignment holdout, not this aggregate, is
the positive evidence. The production default therefore remains `legacy` and
v2 requires an explicit version flag.

## Modulation routing across all 21 released classes

A bounded classical AMR baseline now extracts 53 features from IQ alone:
amplitude statistics, phase-invariant complex moments, centered phase-step
moments, autocorrelation and spectral concentration. It does not receive the
label, SNR, symbol rate, bit truth or filename as predictor input.

The record-level diagnostic selected ridge regularization on validation data
and obtained `937/2646 = 35.4119%` top-1 and `1466/2646 = 55.4044%` top-3.
A stricter transfer split held out complete modulation/SNR/rate cells and
obtained `154/546 = 28.2051%` top-1 and `271/546 = 49.6337%` top-3. Balanced
random baselines are 4.7619% and 14.2857%. An independent audit reproduced
both experiments and verified the information boundary.

Both AMR results are labelled post-development. The dataset had already been
processed by earlier BER work, and the group experiment was rerun after
provenance-only corrections. These numbers justify a research router that
launches the top three decoder families; they do not establish transmission
detection, bit recovery, real-IQ generalization or packet yield.

## Real-IQ AFSK1200

An independent Bell-202 AFSK1200/AX.25 receiver was added. Its path is complex
IQ to FM discriminator, 1200/2200 Hz non-coherent tone energy, a fixed blind
clock bank, NRZI/HDLC, CRC-16/X.25 and strict AX.25 UI parsing. Exact inner
CCSDS parsing is annotation only after AX.25 succeeds. Large CI16 files are
processed in bounded overlapping windows; a regression test prevents
whole-file materialization.

The frozen same-IQ cohort contains 23 captures with exact AFSK/1200 metadata
and catalogued zero frame yield. The generic gr-satellites control and native
receiver both produced zero trusted frames. All-zero IQ, wrong-baud and sample-
permutation controls also produced zero across all 23 captures. The verdict is
`zero_zero_parity_not_victory`: the plugin adds coverage and fail-closed
behavior, but this cohort contains no positive recall evidence.

## Negative acquisition result

A separate preregistered estimator tried to replace truth-aided whole-record
alignment using only the blind timing phase already emitted by the waveform
receiver. On 768 holdout records it produced BER `0.480377`, slightly worse
than zero shift at `0.479565`; oracle +/-8 reached `0.459433` and oracle
+/-1024 reached `0.240509`. The hypothesis is rejected. Timing phase identifies
position within a symbol but not the integer number of symbols preceding the
first recovered decision. No production code was changed from this null.

## Next research gate

The next acquisition method must estimate a whole-record symbol origin without
truth, for example from an independently detected burst boundary, a preamble,
or a calibrated deterministic DSP delay. Carrier/timing v2 should then be
tested on unseen real positive IQ with frame-level validation. Additional
PSK/QAM/APSK, AX.100 and CCSDS AOS/USLP plugins remain explicit missing
coverage; they must be added and evaluated rather than guessed.

Primary evidence:

- `reports/rml24-carrier-timing-v2.md`
- `reports/rml24-carrier-timing-v2-independent-audit.md`
- `reports/rml24-physical-ber-carrier-timing-v2-v1.md`
- `reports/rml24-amr-feature-baseline-v1.json`
- `reports/rml24-amr-group-transfer-v1.json`
- `reports/rml24-amr-independent-audit.md`
- `reports/rml24-acquisition-v2-findings.md`
- `reports/polyitan-afsk1200-ax25-same-iq-v1.md`
