# Metadata-feasibility amendment, 16 September 2026

Declared before fetching or decoding the new cohort waveforms. The original
[protocol](../config/archive-qualification-20260916.json) remains unchanged.
The [amended protocol](../config/archive-qualification-20260916-amended.json)
changes only `per_mission`, from 32 to 40. The target remains 96, the station cap
remains 12, and dates, mission list, grouping, rank salt, minimum diversity,
decoder budgets and worker counts remain unchanged.

The complete NORBI and UNISAT-7 metadata chains show:

| Mission | Station 1710 | Station 2029 | Station 4649 | Total metadata records |
|---|---:|---:|---:|---:|
| NORBI | 9 | 11 | 2 | 22 |
| UNISAT-7 | 12 | 8 | 0 | 20 |

With the shared station cap of 12, these two missions together can contribute
at most 26 observations. The original 32-per-mission cap on each of the other
two missions therefore gives a hard upper bound of `26 + 32 + 32 = 90`, below
the target of 96, even before pass grouping or earlier-exposure exclusions.

Raising the mission cap to 40 removes that mathematical impossibility without
increasing single-station concentration. It does **not** guarantee that all
remaining selection gates can pass. The complete API catalogue must still be
obtained before final selection; no rate-limited prefix will become the cohort.
The catalogue amendment binds its original catalogue/protocol hashes and reason.
It must be applied before waveform downloads, with original artifacts preserved.

Only permitted metadata availability was examined for this amendment. No
decoder outcome was used to choose an observation, modify receiver parameters
or select a more favorable ranking seed. The exposure inventory's limitations
remain visible; this amendment does not certify independence or publication
readiness.
