# Exact-job pilot deployment

Verified 2026-09-10 14:39 UTC. This is a separate predictor validation pilot, not a revision of the registered monthly scheduling experiment.

The read-only acquisition completed at 14:32:45 UTC and durably committed two future observation predictions. The current source is pinned by `tooling.sha256`; do not edit that source or rewrite its pilot commitments. Further implementations require a distinct version and separate issuance protocol.

The new `telemetry-yield-exact-job-pilot-v1.timer` is active and waiting for **2026-09-11 22:30 UTC**. It invokes one scoring pass after both observation windows have ended plus the declared 24-hour minimum. The worker is currently inactive, not training or polling. This is a one-time timer, not a recurring collection service or a guarantee that every label will then be available. A failure or incomplete/unknown label must be retained and investigated; neither is automatically a negative reception.

Expected new output: `/home/ubuntu/telemetry-yield/reports/exact-job-v1-20260910/mature-score-20260911/`. That path does not yet contain results. The already completed `early-score-real/` contains zero scored labels and two too-early predictions, with null metrics. The service will not overwrite an existing output directory.

Only two new unit symlinks and the new timer-enable symlink were created. Both units have no drop-ins. Unit syntax verification and the tooling checksum check passed. Worker limits: 1 CPU, 1 GiB memory, 64 tasks, 15-minute timeout, one thread per numerical backend; no API token is loaded. No external observation was submitted and the SatNOGS station heartbeat was not updated.

The existing `telemetry-yield-prospective-v4h.timer` remains active. Its next plan is scheduled for September 11 around 01:19 UTC (timer jitter). Its last service run finished successfully and is inactive. The main campaign still starts September 13 and ends October 14, with zero campaign outcomes so far. This pilot does not add outcomes, elapsed days or claims to that campaign.

Unchanged before/after pilot:

- Main ledger SHA-256: `a7f5370ff6e844ef980c104d795f93c5bc468e059516ef83956d4002f7a57722`.
- Main config SHA-256: `f07c157858b85c55bb25b62df50695bd93678a8b9fa16c80b717279903c53977`.
- Frozen runtime source identity: `7cc358e8be8cb6450363cccd678ed201f68c852ed4f1f8268f05561aa8723213`.
- Exact-job implementation SHA-256: `9b6ab801df016016df23dc8972a25fdcf5c0e74d479acf8c60f913d71a569d3a`.

Scientific boundaries and results are in [research-update.md](/home/ubuntu/telemetry-yield/reports/exact-job-v1-20260910/research-update.md). In particular: current pilot weather is missing rather than borrowed from the September 13 forecast; no gain/noise measurements were fabricated; the existing overlap-based main matcher remains frozen and its outcomes are not silently redefined as exact-window evidence.
