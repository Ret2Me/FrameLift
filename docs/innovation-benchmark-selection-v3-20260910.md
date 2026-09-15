# Station-field correction v3, before any new waveform access

The v2 metadata downloader and its original protocol remain immutable. During its HTTP 429 cooldown, a read-only schema check showed SatNOGS observation rows identify stations as `ground_station`, not `station_id`. The first selector would therefore have collapsed the intended station/day strata to day-only strata. No newly selected waveform had been downloaded and no new decoder outcome had been observed. The first automatic-download continuation was stopped before it could act. This is a disclosed implementation correction, not an unamended preregistered analysis.

Keep the complete metadata collection process and server cooldown unchanged. Do not select from the partially collected prefix. Once its full interval pagination reaches EOF and publishes `metadata-all.json` and `cohort.json`, a new standalone v3 program verifies their recorded identities, prior-exposure snapshot, original frozen selection policy and EOF proof. It then replays the same original rows into **a new acquisition directory**, using the actual positive numeric `ground_station` field. Missing or invalid station metadata is ineligible, not a null stratum.

No date limits, target count (500), SHA256 row/group rank prefixes, round-robin sampling, prior-exposure exclusions, outcome-blindness requirements, file limits, transfer limits, host allowlist or transport retry rules change. No extra API requests are made by the replay. All original files remain available. The old cohort is marked not used for waveform acquisition; the corrected cohort retains the same structural `innovation-benchmark-cohort-v2` schema but a new v3 selection-plan schema and amendment identity. Its actual bytes/order are frozen before downloads and any decoder execution.

Original metadata directory: `work/innovation-benchmark-20260910-v2/acquisition`.

Corrected waveform/cohort directory: `work/innovation-benchmark-20260910-v2/acquisition-corrected-v3`.

The corrected program is `examples/innovation_benchmark_acquire_v3.rs`. A strictly scoped continuation watches only the local original cohort publication, binds metadata PID/starttime/executable identity, verifies the original EOF and metadata identities, then runs the corrected replay/acquisition. It never polls the API and never launches DSP. All terminal failures remain explicit; missing observations are not zero-frame results.
