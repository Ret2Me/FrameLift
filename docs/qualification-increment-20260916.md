# Receiver qualification increment, 16 September 2026

This increment implements the first three development priorities: a native
research workflow, an adaptive trial-ordering experiment, and an independently
selected archival benchmark. It does not certify enterprise readiness, universal
receiver superiority, GPU hardware parity or completion of the separate
observation-planning Python project.

## Acceptance gates

1. Acquisition, cohort freezing, execution, packet validation and reporting have
   supported Rust entry points. External reference decoders and media codecs are
   pinned dependencies, not FrameLift Python orchestration. Existing scientific
   snapshots remain intact. Legacy commands without replacements remain listed
   as migration work rather than deleted or hidden from language statistics.
2. `decode-progressive --scheduler marginal-yield` is opt-in. The default fixed
   order stays available as the matched ablation. Both have the same task bank,
   anchor-generation boundaries, FCS tests and payload parser. Scheduler state is
   replayed from immutable causal decisions and measured results. Tests compare
   exact packets and task details, not just aggregate counts. No gain is assumed.
3. A new archival cohort and executable/configuration identities are frozen
   before decoding its waveforms. Every selected input remains in the report,
   including missing, unsupported, failed and timed-out inputs. Fixed/adaptive
   runs get identical prepared samples and resource settings. Reports distinguish
   standalone losses from additive-union gains and preserve both positive and
   negative results. Signal-confirmed strata are shown separately.

## Scheduler hypothesis fixed before new-cohort decoding

Marginal-yield v1 assigns each completed task the number of previously unseen,
received-FCS-valid AX.25 payload bytes it contributes. Tasks in each four-task
batch are credited in their precommitted order, never in completion order. Stage
priors and quick-window weighting are explicit in the session policy.
The quick sweep visits interval midpoints breadth-first, spanning the recording
before filling gaps instead of limiting a short run to a contiguous prefix.
Measured task wall time estimates cost; it is not isolated CPU time. In later
phases every fourth batch retains the established ordering to sample
less-promising tasks. No task is
pruned in full mode. Early and final channel models retain their existing frozen
anchor generations. Parallelism and restart can change future measured costs;
the journal makes those decisions auditable, not hardware-independent timing.

The scientific question is whether this ordering improves the yield/time curve
on unseen recordings. It is not a claim that adaptive scheduling itself is new.
The old 266-recording CANVAS study and all earlier inspected recordings are
development material. They cannot become holdout data through renaming.

## Status

The native workflow and opt-in scheduler are implemented; engineering checks
have passed. See [validation and campaign status](qualification-validation-20260916.md).
A finalized campaign registration, its actual coverage and results are separate
artifacts. Implementation is not a completed benchmark or publication claim.
