# Comparison with established receivers

[Documentation](README.md) · [Measured results](benchmarks.md) · [Paper](../publication/decoder-paper-v2/README.md)

Telemetry Yield is designed as an **offline recovery component**, not a replacement
for an entire ground-station platform. The most relevant question is whether it
adds useful packets to a specified existing workflow at an acceptable cost.

## Different project scopes

| Project | Established focus | Our relationship to it |
|---|---|---|
| Dire Wolf | AX.25 soundcard modem/TNC and APRS workflows | A packet-radio comparator; our field experiment uses its `atest` 9600-baud path |
| gr-satellites | GNU Radio satellite decoders, SatYAML profiles, live or recorded inputs and telemetry outputs | A reusable decoder ecosystem and controlled replay comparator |
| SatNOGS | Network, scheduling, database, station client and ground-station infrastructure | A recording source/integration context; not one interchangeable decoder executable |
| SatDump | Satellite acquisition, decoding pipelines, instruments, image products and projections | A broader end-to-end processing suite; not an arm in the 266-recording telemetry trial |
| Telemetry Yield | Finite offline receiver portfolios, guarded local adaptation, resumption and evidence | A second-pass worker with narrower field qualification |

Scopes above are based on the projects' own descriptions:
[Dire Wolf](https://github.com/wb2osz/direwolf),
[gr-satellites](https://gr-satellites.readthedocs.io/en/latest/command_line.html),
[SatNOGS](https://satnogs.org/about/),
[SatDump](https://docs.satdump.org/md_docs_2pages_2QuickStart.html).
Reviewed 2026-09-15; these are not frozen software version identifiers.

## What is different here

| Design choice | Practical effect | What it does not establish |
|---|---|---|
| Fixed portfolio with complementary frontends/clocks | One hypothesis may recover a packet another misses | Other receivers also use sophisticated DSP; diversity itself is not new |
| Disjoint CRC-anchor channel transfer | Nearby successful reception can guide a harder window | No reference payload injection; no proof of universal channel adaptation |
| Additive nearest/blind/multi-anchor lanes | Preserve the native baseline while trying extra methods | Does not preserve every packet decoded by an external program |
| Checkpointed quick/deep/full sessions | Reuse committed work when spending more time later | Quick mode does not guarantee full-mode yield or a hard 3-second deadline |
| Byte-level provenance and explicit integrity | Makes additions, losses and regressions inspectable | A checksum is not transmitter authentication or independent replication |
| Optional numerical backend | CPU remains default; FIR can use CUDA | No demonstrated whole-pipeline GPU speedup yet |

MLSE, Gardner timing recovery, weighted regression and predictive residual models
are established techniques. The contribution under evaluation is the specific
system, guarded transfer policy and reproducible recovery workflow. The current
data do **not** isolate which component causes the aggregate gain. Codec
conditioning in particular has a null matched-ablation result in this study.

## What we actually compared

The complete historical trial uses one numerical PCM16 WAV per original OGG:

- Dire Wolf: `atest -B 9600 -F 0 -h`, bit repair disabled.
- gr-satellites: the frozen FSK 9600 / AX.25 G3RUH profile, telemetry submission disabled.
- Native progressive receiver: full configured task bank with a completion gate.
- Two separate native innovations arms, with and without codec information.

The [profile](../publication/decoder-paper-v2/evidence/grsat-profile.yml) and
[binary identities](../publication/decoder-paper-v2/evidence/receiver-freeze.json)
are supplied. The reference union is 4,066 packets; the progressive result is 6,220,
with 2,158 additions and four misses. Those counts are **not** the original native
SatNOGS station's packet totals, an equal-CPU comparison, or the maximum possible
yield of either competing project under every configuration.

External decoders remove FCS from their exported PDUs; this trial relies on their
internal check. Native received FCS is retained and rechecked. That evidence
asymmetry is documented, not hidden behind identical labels.

## Comparisons we do not claim

No controlled SatDump result is included in the headline telemetry trial. Image
examples require their own waveform, pipeline version, image assembly policy and
missing-pixel mask. Visual enhancement cannot be presented as recovered pixels.

Dire Wolf's [WA8LMF results compilation](https://github.com/wb2osz/direwolf/blob/dev/doc/WA8LMF-TNC-Test-CD-Results.pdf)
and the [original test-medium description](http://wa8lmf.net/TNCtest/index.htm)
are useful benchmark context, not a numerical baseline for our different
9600-baud satellite corpus. This paper reports **no Telemetry Yield score on that
medium**. Its version, tracks, frontend settings and counting rules would need
to be fixed before a valid comparison.

## Product fit

Use Telemetry Yield when stored recordings and additional offline compute are
available, mission parameters are known, and traceable incremental recovery is
valuable. Keep existing acquisition, station operations and mission interpretation
systems where they already meet the requirement. Evaluate the combined workflow
on your own independently selected recordings before promising a business outcome.
