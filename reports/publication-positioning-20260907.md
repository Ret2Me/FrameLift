# Publication positioning: offline telemetry recovery

Date: 2026-09-07. Bounded primary-source comparison, not an exhaustive novelty review. This note changes no frozen implementation, protocol, manifest, cohort or result. No blind IQ or campaign outcomes were inspected for this task. Proposed follow-up experiments below do not amend the running experiment.

## Bottom line

The defensible claim is an **evaluated recovery system for difficult archived FSK-family/AX.25 receptions**, not invention of phase demodulation, offline replay, soft decisions or CRC-guided repair. There may be publishable engineering/experimental value in the particular combination and its measured operating tradeoff. A new general demodulation algorithm, state-of-the-art performance and universal protocol support are not established.

## What the inspected implementation actually does

Read-only source inspection covered `src/telemetry_yield/clipping_robust_fsk.py`, `clock_recovery.py`, `soft_sync.py`, and `releases/blind-phase-confirmatory-v2/config/candidate-config-v4-3-final-r4.json`. A read-only Graphify query located the corresponding symbols; its truncated graph output was used for navigation, not as proof of implementation details.

- Selects windows using signal statistics, without reference payloads or known event timestamps.
- Tries a constant-envelope reconstruction heuristic only at exact CI16 saturation endpoints, alongside phase-discriminator variants. This is model-dependent declipping, not recovery of arbitrary clipped waveforms.
- Discriminates phase before its low-pass filter; ranks a bank of timing hypotheses by an unsupervised eye score. The frozen candidate uses lags 1/3, 64 timing phases, top 16 hypotheses, and **only zero rate-error offset**. Its DC/trend removal is not an explicit broad CFO-search bank.
- Uses bounded reliability-ranked, G3RUH/NRZI/HDLC-aware raw-symbol repair, with CRC-16/X25 and AX.25 structure checks. Soft magnitudes are not calibrated likelihood ratios. Multiple frontends process the same IQ and therefore are not independent truth witnesses.
- The inspected frozen candidate covers FSK/GFSK/GMSK catalog families and AX.25/plain or G3RUH framing, at four configured rates. It does not demonstrate generic CCSDS/all-protocol support. Configuration explicitly leaves repaired output untrusted and publication readiness false.

## Closest precedents and the remaining distinction

| Primary precedent | Established capability and implication |
| --- | --- |
| [GNU Radio quadrature discriminator](https://www.gnuradio.org/doc/doxygen/classgr_1_1analog_1_1quadrature__demod__cf.html); [gr-satellites FSK implementation](https://github.com/daniestevez/gr-satellites/blob/main/python/components/demodulators/fsk_demodulator.py) and [offline CLI](https://gr-satellites.readthedocs.io/en/latest/command_line.html) | Differential phase demodulation is standard. gr-satellites already provides filtering, DC blocking, Gardner timing recovery and soft-symbol output, with offline IQ operation. The candidate's filtering order, clipping hypotheses and bounded replay policy require ablations; “soft/offline versus hard/live” is not an accurate novelty contrast. |
| [SatNOGS gr-satnogs](https://gitlab.com/librespacefoundation/satnogs/gr-satnogs); [satnogs-decoders](https://gitlab.com/librespacefoundation/satnogs/satnogs-decoders) | Compare an exact receiving flowgraph, demodulator version and mission parameters. The similarly named satnogs-decoders project supplies telemetry payload parsers; it is not itself the IQ demodulation comparator. SatNOGS and gr-satellites are not interchangeable names for one implementation. |
| [Dire Wolf HDLC repair](https://raw.githubusercontent.com/wb2osz/direwolf/master/src/hdlc_rec2.c); [9600-baud demodulator](https://raw.githubusercontent.com/wb2osz/direwolf/master/src/demod_9600.c) | Existing packet-radio software replays raw bits, repairs candidate errors, reruns NRZI/G3RUH processing, and validates CRC plus protocol sanity. It also supports multiple slicers. Thus CRC-checked repair and hypothesis diversity are not new; reliability ordering, syndrome pruning and exact line-code/framing constraints are the relevant comparison. |
| [Chase, 1972](https://doi.org/10.1109/TIT.1972.1054746) | Reliability-guided trial patterns for soft decoding are longstanding. The present implementation is not thereby an implementation of Chase decoding or a new general soft-decoding principle. |
| [Duffy, An and Médard: ORBGRAND](https://arxiv.org/abs/2202.13951) | Reliability-ordered noise guessing with code-membership tests is an established modern family. A bounded protocol-constrained search is related in concept, but no equivalence to ORBGRAND or maximum-likelihood optimality is shown here. |

Inference from these comparisons: the strongest specific contribution could be the integration of reference-free acquisition, saturation-aware frontends, protocol-aware bounded repair, provenance and measured false-positive control. Establishing algorithmic novelty would require a deeper comparison than this bounded review.

## What the Nature dataset can establish

[Zhang et al., Scientific Data 13, article 860 (2026)](https://www.nature.com/articles/s41597-026-07182-7) describes RML24 as hybrid GNU Radio/USRP X410 laboratory RF acquisition plus simulated channel effects, explicitly not genuine satellite-pass recordings. Records are 2048 complex samples at 1 MHz: 2.048 ms. The paper lists 22 modulations and 1,386,000 records; PKLformat.zip includes transmitted bit labels. The data DOI is [Zenodo 17800058](https://zenodo.org/records/17800058).

The count difference is explained, not an unexplained project exclusion: the [authors' README](https://raw.githubusercontent.com/yiwawa/RML24-Cognitive-Radio-for-Satellite/main/README.md) says the deposited version omits problematic SOQPSK-PM and contains 21 classes. Existing local inventory `reports/rml24-hdf5-inventory-v1.json` records a complete scan of **1,323,000 records**, 1323 groups of 1000, and classes 0–20. These are the actual audited corpus counts. Its symbol-rate labels include 250 kBd, whereas the paper gives 200 kBd; retain file-derived labels and identify the deposited version. Only small existing metadata reports were read for this reconciliation, not IQ arrays or scientific outcomes.

It is useful for BER versus SNR/modulation and synchronization robustness. Raw-bit records are not automatically complete AX.25 or CCSDS telemetry frames. Consequently, lower BER does not establish additional valid satellite packets, and zero packets from a protocol decoder on unframed data is not decoder failure. Transmitted-bit alignment must remain an explicitly declared scoring operation, never information supplied to blind acquisition.

## Minimum fair evidence for a submission

1. Complete the frozen held-out paired comparison. Preserve all exposures/exclusions and the planned primary endpoint; do not tune from its outcomes. Publish a precise denominator and failure accounting, not an undefined overall “success percentage.”
2. Feed identical original IQ and equally available mission information to exact pinned SatNOGS/gr-satellites configurations. Include a development-tuned baseline with a declared parameter/computation budget. For the compatible AX.25 subset, Dire Wolf is a useful repair comparator.
3. Separate candidate-only performance from the union with baseline output. Report extra **and missed** unique payloads, recovered observations and payload bytes under explicit deduplication rules. A baseline fallback preserves its yield by construction; that alone does not prove a better standalone receiver.
4. Add separately declared ablations: no declipping; no phase/timing bank; no repair; and the full method. Report yield against CPU time, memory and latency, rather than attributing gains from unlimited retries to intrinsic demodulator efficiency.
5. Keep native and repaired frames separate. Empirically measure the full pipeline on appropriate signal-free/null controls, and verify repaired payloads against known transmissions or independent reception where possible. CRC-16 acceptance after many adaptive trials is not an independent authenticity guarantee; same-IQ consensus does not solve that problem.
6. Report paired uncertainty clustered by pass/recording, and generalization across stations/satellites where supported. Keep the RML BER experiment a distinct supporting endpoint. Release reproducible code/configuration, admissible inputs and provenance sufficient to repeat the comparison.

A reproducible gain with controlled false positives could support a useful systems/methods paper and a station-side offline recovery service. It would still not demonstrate universal receiver superiority, nor guarantee publication or deployment readiness.
