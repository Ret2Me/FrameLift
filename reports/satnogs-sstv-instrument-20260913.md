# SatNOGS SSTV reference and scientific-instrument recordings

Completed 2026-09-13. No demonstrated SSTV recovery gain is claimed.

## SSTV comparison

Used the official `gr-satnogs` `sstv_pd120_sink`, unmodified source at commit `4defcfc445c1f94450c48ad348689e7986ebe6f7`. The audio-domain frontend reproduces the blocks and parameters in `satnogs-flowgraphs/generic/sstv_pd120_demod.grc`, commit `ac12b77974a4478fb4f24ae8b41bf74b808fb03a`: Hilbert 65/Hamming, 1750 Hz translation with 5× decimation, NBFM 600 Hz deviation and 75 us deemphasis, lowpass, rational resampling to 5263 Hz, official image sink. Input audio is resampled from 12 kHz to 66560 Hz before the unmodified audio path.

This is an offline comparison on the same post-FM audio, NOT a recreation of the original RF acquisition chain or proof of a particular production station's software revision. We excluded five PD180 recordings because the selected SatNOGS decoder is PD120-specific.

The two real ISS PD120 recordings (cases 6 and 7 of the previously frozen seven-recording corpus) both yield readable 640×496 images through SatNOGS. Our frozen Rust receiver also yields 496 rows on both, rerun here in 2.151 and 2.274 seconds (not matched total baseline timing). Each native run has 247 observed sync pairs and one predicted pair. Earlier weak Python-reference blur does not recur with the proper reference. Small alignment/detail differences are visible; no ground-truth pixel accuracy or significant recovery gain established. These pairs are excluded from the positive-gain website gallery.

Compiled the original sink, metadata and decoder source directly with GNU Radio runtime/blocks and libpng; no scientific decoder source changes. Full-project build had unrelated hamlib++/Boost test configuration issues; the standalone block harness avoids those components. Reference sink PNGs reproduce byte-for-byte on repeated invocation of the same prepared float stream. Frontend rerun reproducibility has not separately been measured.

Evidence: `work/sstv-established-20260913/satnogs-comparison.json`, `reference-audio.py`, `sink-main.cc`, pinned source checkouts and original/repeat PNGs. Fresh native output directories: `native-6`, `native-7`.

## Instrument imagery

Downloaded 423223596-byte real Meteor-M2-3 IQ WAV from SatDump: `https://www.satdump.org/files/Meteor-M/LRPT/aang23_m23_baseband_137100000Hz_10-38-27_09-08-2023.wav`. SHA-256 `0cf77d57668e93caaae3f25ac90ff6dba28859791869c98703f2b73a6b65e285`; PCM16 stereo IQ, 150 ksample/s.

SatDump 1.2.2 `meteor_m2-x_lrpt` produced CADU and MSU-MR channels 4/5/6 at 1568×4272, then terminated SIGSEGV during later processing. Calibration disabled because analog telemetry was absent. This is a useful real input with visible data gaps, not a completed clean benchmark or a result of our receiver. No native LRPT instrument reconstruction was added in this turn.

Also verified partial HTTP availability for NOAA-19 HRPT 5.42 GB, MetOp-B AHRPT 10.75 GB and Aqua MODIS DB 12.99 GB. Only Meteor fully acquired; no asserted SAR/Terra/Aura sample. Exact URLs and format caveats are in the website asset `instrument-recordings.md`.

## Website telemetry

Seven CELESTA-labelled observations are visualized using actual strict AX.25 packet lists, paired with gr-satellites on identical IQ: 18 reference PDU, 123 native PDU, 105 additional PDU, 26460 additional PDU bytes summed per observation. This is not a global uniqueness count. Independently rechecked retained artifact hashes, received FCS and exact frame-set comparison during asset generation.

The frozen raw-KISS-repeat preregistration FAILED; canonical-frame repetition passed as post-exposure sensitivity. Both results remain visible. Received source FX6FRB and ROBUSTA-1U payload signature are disclosed; no undocumented sensor fields converted into engineering units. This is a selected positive gallery, not a population performance estimate.
