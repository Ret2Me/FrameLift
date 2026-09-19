# Soft-list recovery, observation combining and adaptive scheduling

These receivers are optional development paths. They do not loosen frame
integrity checks and are not enabled by old profiles.

## Reliability-ordered FEC list

`advanced_iq::RecoveryOptions.soft_list` runs only after the ordinary decoder
rejects a candidate. The baseline paths remain:

- LDPC belief propagation with channel LLRs and parity convergence;
- exact 64-state log-MAP for the supported K=7 convolutional code;
- algebraic Reed–Solomon decoding.

The list extension ranks a bounded set of the least reliable decisions. LDPC
reruns use forced channel hypotheses; convolutional candidates use information-
bit posterior probabilities; RS uses received bit reliabilities before algebraic
decoding. Concatenated convolutional/RS profiles pass the log-MAP information
LLRs into the RS list. Every candidate still includes its received CRC/FECF and
must pass the configured independent validator. FEC convergence alone is not a
telemetry frame.

Example profile fragment:

```json
{
  "recovery": {
    "workers": 4,
    "coherent_cpm": false,
    "soft_list": {
      "maximum_hypotheses": 32,
      "unreliable_bits": 10,
      "forcing_llr": 8.0,
      "maximum_work": 100000000000
    }
  }
}
```

The maximum-hypothesis and work limits are checked before decoding. An uncoded
profile does not pretend to gain FEC evidence from list search.

## Combining fragments and stations

`combine-soft-copies` accepts a JSON `soft_combine::Plan` on standard input. Each
copy contains the source SHA-256, observation and station identifiers, sample
bounds, a mission-protected immutable-codeword header and aligned coded-bit LLRs.

```sh
telemetry-yield-rs combine-soft-copies < plan.json > report.json
```

Two overlapping ranges from one source are rejected. Different sources may be
required with `minimum_distinct_sources`; genuinely different stations may be
required with `minimum_distinct_stations`. User labels alone cannot manufacture
source diversity because the content hash remains part of the identity. Copies
must pass the configured correlation gate and share an independently protected
mission identity before their LLRs are added. The combined word then uses the
same FEC, optional list and received-integrity validation as a local word.
Correlation, summation and FEC/list work are charged before decoding; inputs are
bounded to 16 copies and 65,536 coded bits.

This interface consumes already synchronized LLRs. Finding corresponding bursts
across files and estimating relative timing/frequency remain upstream tasks.

## Unresolved-only scheduler

The existing `marginal-yield` scheduler changes task order but deliberately runs
the complete bank. The new opt-in policy performs real gating:

```sh
telemetry-yield-rs decode-progressive ... --scheduler unresolved-only
```

All quick windows are evaluated first. If quick decoding emits at least one
received-FCS AX.25 UI frame in a window, later expensive tasks for that window
are recorded as skipped. Windows with no quick frame retain the entire configured
bank. The causal schedule journal deterministically reconstructs both executed
and skipped sets after a restart.

This policy can miss an additional weak transmission in a window that already
contains one easy frame. It is therefore a deployment latency/cost option, not a
zero-loss publication comparator. Use `fixed` or `marginal-yield` for exhaustive
paired yield studies.

## GPU estimate contract

`estimate-gpu` evaluates explicit measured stage times with an assumed speedup
per stage and fixed transfer/startup overhead:

```sh
telemetry-yield-rs estimate-gpu < scenario.json
```

It is an Amdahl model (`sum(cpu_stage / assumed_speedup) + overhead`), not a GPU
benchmark. Current CUDA code accelerates FIR only; acquisition, sequence
detection, FEC, list search and validation still execute on CPU. A future GPU
implementation should batch candidates/codewords so transfer and launch costs do
not dominate short frames, and must pass exact acceptance and end-to-end parity
qualification on physical hardware.
