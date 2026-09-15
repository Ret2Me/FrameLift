# PSK publication-test readiness — development checkpoint

This concerns the new native BPSK/QPSK/OQPSK extension only. It does not change
the separately frozen SatNOGS progressive FSK/audio campaign or protected
RML24/CANVAS holdouts. No new final publication test is silently substituted
for an exposed development experiment.

## Current evidence and decision rules

| Gate | Evidence available | Remaining requirement |
|---|---|---|
| Executable/configuration identity | Candidate13:392 library and17 CLI tests pass; frozen source/binaries and before/after hashes; reviewed OGG/WAV input-integrity repair | Freeze the exact final experimental configuration and comparator, not just an executable; no deployment inferred |
| PSK backward compatibility | Pinned09→10 default and10 retained portfolio banks exact across144 configurations;10→11 and10→13 all288 banks also exact | Preserve these gates on future changes; no relabeling old studies as fresh tests |
| Valid packet endpoint | Received CRC/FCS/FECF and exact bytes; separate raw versus strict AX.25 structure | Use the same packet contract for both arms; do not count plausible headers or LDPC convergence as validated telemetry |
| Synthetic robustness | 83520 calls,173 extra exact positive-case recoveries, no lost positives | 99 high-SNR SPS8 misses remain; selected ablation is diagnosis, not a repaired independent holdout |
| Real BPSK | Same-window comparator match on original nine cases; additional selected clips show gains and losses under symmetric raw-FCS accounting | Freeze a stronger common scheduling/acquisition comparator before fresh observation selection |
| Real QPSK | Audited actual integrated matched-window test0→88/88,590 eligible cells,24 fresh noise controls empty | Separate external FEC dependency and engineering-transmitter source from orbital telemetry; fresh independent orbital cohort still required |
| Real OQPSK | Independent GNU-transmitter simulations and timing/carrier diagnostics | Compatible real complex-IQ recording with independently verified received integrity and frame layout |
| False positives | Full-bank synthetic noise/bad-CRC controls; original-packet CRC restorations explicitly separate | Larger independently selected noise/interference controls for the final frozen search space |
| Runtime/resource cost | Full work accounting, recorded shared-host timings | Paired balanced same-host timing with identical inputs, scheduling, output contract and reported setup/I/O/grading costs |
| Generalizable publication result | Audited development results with preserved failures and source provenance | Prospectively selected untouched cohort, fixed stopping/exclusion rules and clustered observation-level analysis |

Broad **all-PSK final qualification is NO-GO** while real OQPSK and stressed
SPS8 gaps remain. An engineering methods paper can already describe these
negative and positive development findings honestly; they do not establish
a revolutionary algorithm or a population-wide additional-telemetry rate.

## Next final benchmark contract after the remaining repair gates

1. Freeze executable, exact configuration portfolio, maximum compute, packet
   equality contract, common window schedule and comparator versions. Separate
   unchanged baseline from each added mechanism so a larger search budget is
   not presented as a fair equal-cost speed advantage.
2. Select an untouched cohort from metadata alone, with explicit date interval,
   modulation, symbol-rate, receiver representation and integrity support.
   Keep the manifest and exclusion reasons before inspecting recovery outcomes.
   Use both successful and unsuccessful observations, not only promising signals.
3. Keep orbital IQ, engineering-transmitter IQ and synthetic data as different
   strata. FM-demodulated OGG is not interchangeable with complex PSK IQ.
   Do not spend the protected existing holdouts on another repair iteration.
4. Run full declared banks and negative controls. Errors, missing files and
   resource rejection are reported separately from a completed zero-frame decode.
   Preserve partial journals and immutable amended plans if infrastructure fails.
5. Primary outcome: additional/lost exact validated packet bytes per observation
   against the same-input comparator, with a separate count of unique messages
   and retransmissions. Report both wins and regressions. Payloads sharing an
   observation, spacecraft or station are not independent experimental samples.
6. Publish paired observation-level summaries and uncertainty that preserves
   those clusters. Report runtime distributions and a quality-versus-compute
   frontier; do not turn hundreds of correlated demodulator hypotheses into
   hundreds of independent successful receptions.

These are gates and an analysis contract, not a newly executed preregistered
holdout or a claim that unavailable spacecraft formats have been implemented.
Detailed immutable evidence is linked from
[the repair report](psk-demodulator-repair-20260912.md).
