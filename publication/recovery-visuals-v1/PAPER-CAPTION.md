## Proposed figure caption (for the next manuscript version)

**Content-level recovery from the same historical satellite recording.**
CANVAS observation 14780429 (station 2865, 2026-08-14 00:31:48 UTC;
326.364 s original Ogg/Vorbis audio). Left: the union of Dire Wolf and
gr-satellites replays recovers two complete APID-0x20 beacon PDUs. Right:
the frozen progressive receiver recovers 36, including both reference PDUs
and 34 additional beacons. Points are raw solar-panel and battery temperature
fields parsed from actual received packet contents using the pinned SatNOGS
CANVAS definition; no undocumented conversion to degrees Celsius is applied.
The horizontal axis is packet mission-elapsed time relative to 829763 s,
not audio position. Identical axes and point styling are used; missing samples
are not interpolated. Availability ticks refer to recovered-union timestamps,
not the unknown set of all transmitted beacons. This observation was selected
post hoc to illustrate a recovery mechanism, not to estimate population gain.
It lies outside the paper-v1 189-observation interim snapshot. Native received
FCS checks and exact-byte comparisons were repeated for this illustration;
application checksum verification and spacecraft authentication are not claimed.

## Interpretation

The added measurements expose a time-varying sensor trace that cannot be
inferred from the two reference points alone. They do not increase sensor
precision or image spatial resolution. This is a content-level example,
not a new demodulation benchmark or proof that the receiver wins on every input.
