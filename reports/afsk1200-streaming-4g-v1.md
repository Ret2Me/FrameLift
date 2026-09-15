# AFSK1200 deployment streaming gate: 4 GiB

The full 4 GiB test now passes at **146,493,440 bytes peak RSS** against the
fixed 512 MiB limit. All 1,872 overlapping windows completed, the complete
logical input was hashed, no swap was used, and the all-zero negative input
produced no candidates.

The first full run failed at 4,428,591,104 bytes RSS. The decoder did not copy
the entire file into one NumPy array, but it retained one whole-file memory map
while touching every page. With available RAM those clean mapped pages remained
resident and invalidated the bounded-memory claim. The implementation now maps
and closes only the current window (or one permutation block), and its test
asserts bounded offset/shape mappings rather than merely bounded slices of a
whole-file map.

This is deployment evidence, not receiver-recall evidence. The sparse zero
artifact deliberately exercises complete streaming, source hashing,
checkpointing and the degenerate signal path. Candidate-volume bounds are
covered separately by unit and integration tests.

Machine-readable measurements and reproduction details are in
`reports/afsk1200-streaming-4g-v1.json`.
