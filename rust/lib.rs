//! Standalone Rust receiver. Python is a frozen test oracle, never a runtime.
/// Opt-in, signal-only cross-window sequence receiver development pipeline.
pub mod adaptive;
pub mod afsk_legacy;
pub mod anchors;
pub mod audit;
pub mod backends;
pub mod benchmark;
pub mod campaign;
pub mod cli_limits;
pub mod clipping;
pub mod clipping_file;
/// Disjoint multi-anchor calibration for the experimental codec-aware lane.
pub mod codec_pool;
/// Experimental original-codec features, not reconstructed error truth.
pub mod codec_reliability;
pub mod coded;
pub mod compat;
pub mod compute;
pub mod convolutional;
pub mod cw;
pub mod dsp;
pub mod fec;
pub mod formats;
pub mod generic;
pub mod geoscan;
/// Experimental predictive colored-noise sequence detector.
pub mod innovation;
pub mod innovation_audio;
pub mod input;
pub mod ledger;
pub mod meteor;
/// Opt-in established Mueller-Muller real pre-clock timing detector.
pub mod mm_clock;
pub mod physical;
pub mod progressive;
pub mod progressive_audio;
pub mod progressive_channel;
pub mod protocol;
/// Coherent IQ PSK frontends with explicit ambiguity and timing hypotheses.
pub mod psk;
pub mod receiver;
/// Opt-in robust likelihood experiment; not enabled by receiver defaults.
pub mod robust_sequence;
pub mod sequence;
pub mod soft;
pub mod space_link;
pub mod space_packet_stream;
/// Opt-in development DSP; not used by the qualified receiver defaults.
pub mod tracking;
pub mod triage;
/// CPU-only experimental predictive channel uncertainty; not in default paths.
pub mod uncertainty;
pub mod uncertainty_audio;
