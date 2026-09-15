# Advancement gates after the September 10 development experiment

This is a post-development next-step plan, not retroactive preregistration of
the completed twenty-recording experiment or the 32 synthetic paired files.

## What advances, and what does not

1. Keep guarded channel refitting and AR innovations as complementary offline
   receiver candidates. Preserve all previous full-progressive output, including
   blind and multiple-anchor branches, when integrating a future versioned
   task bank. The current experimental example is not itself that full bank.
2. Keep the compressed-packet-density variance branch disabled by default.
   A model that fails its own residual check must not run merely to improve a
   frame count. No codec-specific benefit has yet been demonstrated.
3. Do not market ordinary colored-noise sequence detection as novel. Any
   scientific novelty claim needs both a defensible prior-art boundary and an
   isolated contribution beyond matched white/AR receiver lanes.

## Resolve the negative compressed-audio control first

The same fixed generated frames improved at the raw WAV symbol level but not
with the complete OGG receiver. A separate full-receiver run on their unchanged
lossless WAV counterparts can distinguish frontend/timing/transfer limitations
from additional codec effects. It remains development evidence.

Inspect guarded source and target residual spectra and covariance on these
known synthetic signals **after decoding**. Oracle target bits may diagnose
which assumption failed; they must not select parameters reported as blind
target performance. One plausible hypothesis is covariance nonstationarity:
low-noise anchors and weak noisy targets have different codec/radio mixtures.
This explanation is unproven and must not replace the measured negative result.

The codec candidate needs richer, physically supported features before an
expensive new field sweep: explicitly parsed transform/block evidence and
propagation through frontend support, not interpreting packet byte density as
known quantization noise. Any learned model should be trained on disjoint
controlled encode/decode pairs and frozen before genuine field holdout scoring.
This is a proposal, not an implemented exact Vorbis covariance model.

First, diagnose the local calibration coverage: in the twenty-file field run,
193 of 243 source models lacked packet diversity, density variation or residual
energy; only 50 reached the predictive score check, and all 50 failed it. These
counts include repeated source frames from overlapping windows, not 243
independent transmissions. One bounded next candidate is pooled calibration
from multiple disjoint CRC-valid source intervals, with per-source training-only
normalization and strict target-window exclusion. It should retain the same
predictive gate, not lower the threshold until some trial runs. Separate the
benefit of obtaining any usable calibration from actual frame-recovery benefit.

## Independent field evaluation

- Freeze executable, task bank, budgets and acceptance rules before inspecting
  the new outcomes. Select recordings by time/profile/availability, not by
  success of the experimental receiver.
- Use identical original OGG and decoded-PCM identities across applicable
  receivers. Keep decoding separate from archived reference-byte scoring.
- Include previously successful and unsuccessful recordings. Unsupported
  modulation/protocol, acquisition failure, timeout and completed zero-frame
  decoding are distinct outcomes.
- Report paired observation–PDU gains/losses and corpus-global unique data
  separately. Cluster uncertainty by satellite pass/station dependence;
  overlapping windows and repeated receptions are not independent trials.
- Compare white/AR/codec lanes on the same channel/timing/gain bank, and the
  complete deployed baseline plus additive experiment at matched budgets.
- Include learned-anchor noise-only and interference controls. Received CRC
  and plausible CCSDS structure are useful checks, not authentication or a
  population false-alarm guarantee after many hypotheses.
- Separate offline future-anchor recovery from causal live latency. The new
  experimental integration does not yet participate in quick/deep checkpoints.

Advance codec conditioning only if it yields reproducible additional correct
frames beyond ordinary AR under these controls. Otherwise publish the negative
ablation honestly and focus the technical contribution on measured recovery,
provenance and computational behavior without claiming a new detection theorem.
