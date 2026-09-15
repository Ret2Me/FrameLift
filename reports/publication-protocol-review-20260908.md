# Receiver publication protocol review — 2026-09-08

Status: prospective design review; no holdout results inspected for this review.
Working manuscript: `docs/paper-satnogs-archive-recovery-v1.md`.

## Highest-priority gates

1. Freeze the metadata-only cohort and complete development exclusion list before
   downloading/inspecting selected audio outcomes. The target 336 recordings over
   14 dates is a resource plan, not proof of adequate independent sample size.
2. Freeze decoder binaries, exact settings, archive scope, endpoints, control
   exposure, analysis, and stopping/replacement rules. Preserve any later amendment
   and treat already exposed outcomes as development for modified methods.
3. Separate same-audio gr-satellites component replay from historical SatNOGS
   archive results. Pin the reference version and profile. Do not claim the
   development 5.24% gain is versus SatNOGS: it is versus our previous receiver.
4. Report globally deduplicated archive-incremental content as well as local
   recoveries. Describe the exact archive snapshot scope; selected observations
   do not constitute the entire worldwide database. Missing archive files weaken
   absence claims and cannot be treated as empty results.
5. Validate incremental packets using original received FCS plus an independent
   computation and waveform provenance. Replay proves repeatability, not an
   independent reception. Address plausibility does not authenticate origin.
6. Apply the complete search to independent negative/known-truth controls and
   report exposure. Include paired cluster-aware uncertainty, date sensitivity,
   and matched compute accounting; do not count the same broadcast repeatedly
   as independent evidence.

## Concrete prior-art issue

The portfolio principle is not new by itself. Dire Wolf officially supports
9,600-bps GMSK/G3RUH and has long documented multiple demodulators. Add an offline
version-pinned comparison with repair disabled if practical; otherwise disclose
that this existing alternative was not evaluated. Established Gardner timing
recovery and DC blocking also require attribution. The candidate contribution is
rigorously established archive recovery/cost evidence and its reproducible method,
not invention of these building blocks.

Primary sources checked on 2026-09-08:

- [gr-satellites components](https://gr-satellites.readthedocs.io/en/latest/components.html)
- [GNU Radio timing-error detector source](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/lib/timing_error_detector.cc)
- [GNU Radio DC blocker source](https://github.com/gnuradio/gnuradio/blob/main/gr-filter/lib/dc_blocker_ff_impl.cc)
- [Dire Wolf project](https://github.com/wb2osz/direwolf)
- [Dire Wolf revision history](https://github.com/wb2osz/direwolf/blob/master/CHANGES.md)
- [SatNOGS Network documentation](https://wiki.satnogs.org/Network)

## Scope of a supportable paper

A one-mission independent test can support a narrowly stated experimental case
study. It cannot establish support or superiority across all satellite protocols.
Multiple stations strengthen realism but are not automatically independent
transmissions. Four selected development cases closing baseline gaps are useful
debugging evidence, not general parity. A positive effect alone does not establish
scientific novelty, and no design can guarantee acceptance. Null or negative
results must remain visible. Deployment requires a separate operational gate.
