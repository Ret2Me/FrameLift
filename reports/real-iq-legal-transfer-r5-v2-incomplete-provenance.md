# R5 v2 provenance interruption

Verdict: **INCOMPLETE_FAIL_PROVENANCE**.

The frozen CRC source expected `1a31486da4d15fec4f172e4856d2cdf2b91092ff565b14d55d4d752fb6423cee` but changed to `f93c4eca1f91e3d71e30606a90a988a16b2c660e44a8e307181c160bc07849a7` at 2026-09-03T01:02:29.391573+00:00. All-zero completed before the drift. The full-reversal run crossed the drift boundary, so its complete provenance is indeterminate even though the code change is an algebraically equivalent table optimization. No R5-v2 exposure is counted toward acceptance. No completed artifact was deleted or reused.
