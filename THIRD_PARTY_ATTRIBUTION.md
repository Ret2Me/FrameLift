# Third-party data and software attribution

This file identifies external material used to produce telemetry-yield
evidence. Raw datasets and external executables are not vendored into the
software artifact unless an explicit release manifest says otherwise.

## Data

- Yi Zhang, Bo Zang, Hongbing Ji, Lin Li, Shiyao Li and Leyan Chen,
  “Cognitive Radio for Satellite TT & C System: A General Dataset Using
  Software-defined Radio,” *Scientific Data* 13, 860 (2026),
  https://doi.org/10.1038/s41597-026-07182-7. Dataset: RML24,
  https://doi.org/10.5281/zenodo.17800058.
- Daniel Estévez, “Test of gr4-packet-modem through an Intelsat 37e C-band
  transponder,” Zenodo record 13371136,
  https://doi.org/10.5281/zenodo.13371136.
- CAMRAS/SatNOGS public IQ recordings, observation-specific provenance and
  source URLs recorded in frozen plans and result artifacts.
- SatNOGS Network observation/transmitter metadata and demoddata used only in
  explicitly labelled baseline, routing and audit roles:
  https://network.satnogs.org/ and https://db.satnogs.org/. SatNOGS identifies
  API data as CC BY-SA; the planning snapshot manifest records the exact source,
  retrieval metadata and license link
  (https://creativecommons.org/licenses/by-sa/4.0/). Recommended attribution:
  “SatNOGS Network observation data and its contributing ground-station
  operators; normalized features and labels modified by telemetry-yield; CC
  BY-SA 4.0.” Libre Space Foundation does not endorse this study.
- Open-Meteo Historical Forecast and Forecast API weather fields, normalized
  to the units recorded in each covariate archive. API data are attributed to
  Open-Meteo under CC BY 4.0; the publication should cite Patrick Zippenfenig,
  “Open-Meteo.com Weather API” (2023),
  https://doi.org/10.5281/zenodo.7970649, identify the upstream model mix, and
  state that telemetry-yield performed time alignment and unit normalization.
  Use of the free endpoint must also satisfy Open-Meteo's current
  non-commercial-use and rate-limit terms: https://open-meteo.com/en/terms.
- Three-hour Kp observations from GFZ Helmholtz Centre for Geosciences under
  CC BY 4.0. Cite Matzka et al. (2021),
  https://doi.org/10.1029/2020SW002641, and the Kp data publication,
  https://doi.org/10.5880/Kp.0001. The archive preserves GFZ's definitive or
  nowcast status instead of relabelling it.
- NOAA Space Weather Prediction Center planetary K-index forecasts. U.S.
  National Weather Service material is public domain unless otherwise noted;
  derived telemetry-yield artifacts identify NOAA as the source and do not
  imply NOAA/NWS endorsement: https://www.weather.gov/disclaimer/.
- CelesTrak GP element sets used for prospective orbit propagation. Every
  response is timestamped and hash-bound, and the client follows CelesTrak's
  documented update/caching limits: https://celestrak.org/usage-policy.php.
- User-authorized PolyITAN IQ bucket snapshot. Object identities, access time,
  byte size and hashes are recorded in `reports/polyitan-live-bucket-*.json`;
  the raw objects are excluded from the software package.

## External receiver software

- Libre Space Foundation and contributors, SatNOGS Network, consulted for the
  scheduling API and historical station/receiver metadata boundary at source
  revision `41e0ab7c1359b1dc18348b9f3bae647ba2b8bf52` (2026-09-03):
  https://gitlab.com/librespacefoundation/satnogs/satnogs-network.
- Libre Space Foundation and contributors, SatNOGS Client documentation,
  consulted for the semantics of `SATNOGS_RF_GAIN`, `SATNOGS_ANTENNA` and
  SoapySDR receiver settings; these are not represented as physical antenna
  gain/type: https://docs.satnogs.org/projects/satnogs-client/en/latest/.
- Daniel Estévez and contributors, gr-satellites, executable reference/backend
  used at the version recorded by each experiment:
  https://github.com/daniestevez/gr-satellites.
- Daniel Estévez and contributors, gr4-packet-modem, executable reference for
  the Intelsat 37e modem capture:
  https://github.com/daniestevez/gr4-packet-modem.
- GNU Radio contributors, GNU Radio runtime and blocks:
  https://www.gnuradio.org/.
- John Langner and contributors, Dire Wolf AX.25 packet modem, used by selected
  golden-corpus adapters: https://github.com/wb2osz/direwolf.

## Optional CUDA compute dependencies

- cudarc 0.19.9 and contributors, optional Rust CUDA driver/NVRTC bindings:
  https://github.com/chelsea0x3b/cudarc. The exact dependency and transitive
  versions are retained in `Cargo.lock`; CPU-only builds do not enable them.
- NVIDIA CUDA Driver API, NVRTC and floating-point intrinsic documentation:
  https://docs.nvidia.com/cuda/nvrtc/ and
  https://docs.nvidia.com/cuda/floating-point/index.html. The local kernel
  compilation check used NVRTC 12.9.86. Driver/NVRTC binaries are not bundled
  into the receiver binary or treated as project-authored source.

## Metadata and formats

- SatYAML transmitter and satellite definitions distributed with
  gr-satellites; exact source revision/hash is recorded in experiment reports.
- SigMF specification and Python validator for format validation:
  https://sigmf.org/.
- Consultative Committee for Space Data Systems recommendations are referenced
  to describe protocol structure. No standard text is copied into the release.

Publication metadata must cite the specific source records used by its final
cohort, not only this aggregate attribution file. User permission attestation
does not replace source identification or scientific citation.
