# README recovery figures

Three self-contained SVGs generated from the verified
[version-2 evidence summary](../../../publication/decoder-paper-v2/evidence/summary.json).
No remote fonts, scripts or external image references are required.

| Figure | Question answered |
|---|---|
| [Packet recovery](packet-recovery.svg) | How many observation-level unique packets did each tested receiver recover? |
| [Data recovery](data-recovery.svg) | How many additional protocol bytes were recovered, and how many observations gained packets? |
| [Signal-labelled subgroup](signal-recovery.svg) | What happened in the separate metadata-defined with-signal subset? |

## Regenerate

From the repository root:

```sh
cargo run --locked --example paper_evidence -- render-readme \
  publication/decoder-paper-v2/evidence docs/assets/recovery
cargo test --locked --example paper_evidence
```

The command verifies the bundle checksums and count arithmetic first. The
[Rust renderer](../../../examples/support/readme_figures.rs) derives displayed
counts, percentages, bar lengths and the 266-observation grid from that summary.
This command writes only the three SVGs, not the paper or frozen evidence.

## Counting rules

- A packet is unique within an observation, not necessarily across observations.
- Reference union means the tested Dire Wolf plus gr-satellites packet sets,
  not the original native SatNOGS station output or two counts added together.
- Byte gain is `402,192 − 1,056 = 401,136`; bytes include protocol headers but
  exclude FCS. They are not application-only measurements.
- Green grid cells indicate at least one addition. Some such observations also
  contain missed reference packets. Cells are grouped by outcome, not time.
- The 24 signal-labelled observations are a subset of the same 266-case cohort,
  selected by frozen metadata rather than native decoder success.
- This is an exposed, single-mission historical study with higher computation
  cost. The graphics do not claim independent or equal-compute superiority.

The older [paper illustration](../../../publication/decoder-paper-v2/generated/yield.svg)
and its recorded checksum remain unchanged.
