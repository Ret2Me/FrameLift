# Multi-mode IQ receiver: engineering qualification

This increment moves the experimental recovery mechanisms from one waveform to
seven digital modulation families. It is not a claim that every FrameLift path
or every satellite mission now supports iterative LDPC decoding. The
[receiver contract](../docs/advanced-iq-receiver.md) defines the boundary.

## Changes

- BPSK, QPSK and OQPSK: explicit rectangular or RRC pulse models. QPSK/OQPSK
  run separate I/Q BCJR recursions, reconnecting FEC feedback in serialized
  codeword order. Quadrant, conjugation and OQPSK delayed-arm hypotheses are
  selected with the received sync, not expected payloads.
- FSK/GFSK/GMSK: phase-discriminator observations, pilot-estimated three-tap
  surrogate and modulation-specific continuous-phase remodulation. Gaussian
  BT is explicit; GMSK requires h = 0.5. This is not an exact coherent CPM
  detector or a claim that discriminator noise is independent Gaussian noise.
- AFSK: FM discriminator followed by mark/space sinusoid-energy fits, then the
  common soft/FEC path. SIC estimates audio phase from independently validated
  received codewords, not transmitter truth. Its bounded timing bank is scored
  on training samples, with one final held-out reconstruction check.
- Shared repeat handling still requires a separately CRC32C-protected immutable
  repeat key and nonoverlapping original samples. Turbo posterior values and
  SIC residual copies cannot count as new independent repetitions.
- New SIC fits residual frequency and complex gain on alternating intervals
  anchored at source-window sample zero. No trial is selected by its holdout
  result; rejected fits leave IQ untouched. The accepted original-frame union
  is retained. These are reconstruction guards, not statistical certification.

The original `bpsk_rectangular` path is deliberately retained. Existing generic
PSK frontends changed only helper visibility; existing audio/progressive and
frozen campaign executables are not replaced. New paths remain CPU-only.

## Fixed development matrix

The native `multimode_iq_benchmark` example records its plan before waveform
evaluation. Seed: `0x2026091671ab`. Ten waveform configurations: BPSK, QPSK,
OQPSK, the same three with RRC rolloff 0.35/span 8, FSK, GFSK, GMSK and AFSK.
For each configuration, four cases in each of six groups: clean, collisions,
identical repeats, constructed complementary-symbol erasures, invalid received
CRC with valid LDPC parity, and noise only. **All 240 files remain in the report.**

The transmitter is separate from the receiver and uses literal published TC128
generator rows, not the decoder parity matrix. It generates an artificial
fixed-sync CSP/LDPC test link, not a named mission profile. Truth is retained
separately and used only by the scorer. Both arms decode the exact same retained
cf32 bytes. At 24 ksample/s, normal records have 8192 samples and repeat records
32768; these are short engineering captures, not satellite passes.

The single arm uses the same new frontend with one BCJR/LDPC pass and no
joint/repeat/SIC branches. The full arm enables those branches and retains the
single-pass result. Neither arm is the entire previous FrameLift release,
Dire Wolf, gr-satellites or SatDump. Work is not matched. Relative gains depend
on this constructed mixture and must not be advertised as an orbital gain.
Erasure fixtures mute pre-modulation symbol levels (AFSK: RF amplitude); they
are stress fixtures, not a validated propagation model.

## Results

The complete 240-file matrix contains 160 positive and 80 negative recordings,
with 200 expected packet identities counted within recordings (not globally
unique byte strings). Exact transmitter-byte agreement is required:

| Group | Recordings | Expected frames | Single pass | Full union | Added | Lost |
|---|---:|---:|---:|---:|---:|---:|
| Clean | 40 | 40 | 40 | 40 | 0 | 0 |
| Two-packet collisions | 40 | 80 | 40 | 75 | 35 | 0 |
| Identical repeats | 40 | 40 | 40 | 40 | 0 | 0 |
| Constructed complementary erasures | 40 | 40 | 0 | 19 | 19 | 0 |
| Wrong received CRC, valid LDPC | 40 | 0 | 0 | 0 | 0 | 0 |
| Noise only | 40 | 0 | 0 | 0 | 0 | 0 |
| Total | 240 | 200 | 120 | 174 | 54 | 0 |

No incorrect accepted frames occurred in either arm, including both runs on
all 80 negative files. The observed **45% relative improvement** is specific to
this constructed mixture and this single-pass comparator. It is not a gain
over the entire prior release, an operational false-alarm bound, or a field
result. The full receiver still missed **26 of 200** expected frame identities.
Frame provenance assigns 35 added packets to residual reacquisition after SIC
and 19 to independent-copy combining. This cohort does not establish a separate
yield advantage for the varying-channel/turbo branch itself.

| Waveform | Expected | Single pass | Full union | Added |
|---|---:|---:|---:|---:|
| BPSK rectangular | 20 | 12 | 20 | 8 |
| BPSK RRC | 20 | 12 | 20 | 8 |
| QPSK rectangular | 20 | 12 | 16 | 4 |
| QPSK RRC | 20 | 12 | 14 | 2 |
| OQPSK rectangular | 20 | 12 | 16 | 4 |
| OQPSK RRC | 20 | 12 | 16 | 4 |
| FSK | 20 | 12 | 20 | 8 |
| GFSK | 20 | 12 | 19 | 7 |
| GMSK | 20 | 12 | 20 | 8 |
| AFSK | 20 | 12 | 13 | 1 |

In particular, the four quadrature configurations and AFSK did not recover the
constructed complementary-erasure group. GFSK missed one erasure case; QPSK RRC
missed two second packets and AFSK missed three second packets in the collision
group. Clean-copy combining is separately verified for every waveform, but
implementation support does not imply success on every erasure pattern.
No case was removed or relabeled after observing these outcomes.

Summed decode-only wall time was 5.6333 s for single pass and 9.6941 s for the
full union. These short fixtures exclude CLI hashing/I/O and were run on a
shared host; this is approximately 1.72× the compute wall time, not a speedup
or a real-time guarantee. Dedicated throughput/equal-budget experiments remain
necessary.

## Artifacts and regressions

Local artifact root: `/home/ubuntu/framelift-multimode-20260916.ycW7uS/`.
`cohort/` contains the plan, all inputs, profiles, truth and results. Frozen
executables in the parent directory have SHA-256 identities:

- Receiver: `9a04404d7e170417d9d3e39d8a8d6c3a87969025dfe2186959e712b244373ae2`.
- Multi-mode benchmark: `5e45fe77728cecedc57d8bccb1a4b97e62f90e65975fdf83c2ae43d6486833a6`.
- Legacy IQ benchmark: `c6fb6ab64b0b03c2095b20cc3d1d66dd8ab772729b7df237fd5bc7ea25221815`.
- Block benchmark: `1010a58472ff1a7cf2aa4e3ea08ef473ac66712a27a46da0523a5005d7bd2fdd`.

`legacy-iq-regression/` reproduces all **336 complete receiver reports** from
the prior 112-file / three-arm IQ experiment byte-for-byte. Its result remains
64 / 64 / 112 frames. `block-regression/` reproduces all 256 positive rows,
per-pass diagnostics, summary and all 64 negative controls from the preceding
block experiment, excluding wall-clock measurements; the stronger single-pass
and turbo counts remain 128 and 138. These are compatibility checks, not new
gain estimates to pool with the 240-file matrix.

Release CLI runs for `afsk-collision-02`, `gmsk-symbol_erasure_repetition-00` and
`oqpsk_rrc-collision-02` produced complete reports exactly equal to their library
counterparts. They returned one, one and two frames respectively. The AFSK
replay deliberately retains the unsuccessful second-packet recovery.

Final verification completed:

- Library: **533 passed, 0 failed, 6 explicitly ignored** local-artifact/helper
  or manual-benchmark tests (411.11 s).
- CLI integration: **25 passed, 0 failed** (397.63 s), including all ten new
  waveform configurations on actual cf32 files. Complete CLI reports equal
  library reports on identical quantized input bytes, with an empty PATH.
- Strict production/library/binary and all three benchmark-example Clippy
  checks passed with `-D warnings`; formatting and `git diff --check` passed.
- Optimized receiver and benchmark builds, generated Rust documentation and
  compile-only `--features cuda` checks passed. No new GPU execution is claimed.

That is **558 passing tests**, not including the six deliberately ignored
special tests. This does not claim a clean all-target strict test-code lint run;
previously documented unrelated test-only lint debt remains outside this task.
The two frozen publication-campaign executable hashes were checked unchanged.
No campaign restart, source commit, GitHub push or production deployment was
performed as part of this increment.

## Development failures and fixes

The first multi-mode collision test exposed biased pilot CFO after GFSK capture.
Subtracting a strong transmission with that frequency left enough residue to
hide the second packet. Training-only residual-frequency estimation fixed the
regression. AFSK then exposed coarse timing and biased short-pilot tone-phase
estimates; training-only timing refinement plus data-aided phase estimation from
the already FEC/CRC-validated codeword fixed the tested collision. The fixture
was not removed or weakened to achieve a pass. Invalid-CRC frames still cannot
reach remodulation. A missing SHA-256 import in the new CLI test was corrected
before the full integration run.

## Remaining boundary

No new field-yield measurement was made here. No modulation-wide 100% recovery,
GPU acceleration of these new stages, real-time SLA, arbitrary-pulse SIC,
automatic repeat grouping, uncoded HDLC adaptation, RS/convolutional soft
feedback, or publication readiness is claimed. Analog SSTV and CW do not become
LDPC links. Use mission-specific profiles and independently held-out real IQ
before promoting a mode to deployment or making comparisons with other products.
