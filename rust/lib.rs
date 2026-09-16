//! Standalone Rust receiver. Python is a frozen test oracle, never a runtime.
pub mod acquisition;
/// Opt-in, signal-only cross-window sequence receiver development pipeline.
pub mod adaptive;
pub mod advanced_iq;
pub mod coherent_cpm;
pub mod recovery_code;
pub mod recovery_guard;
pub mod recovery_hdlc;
pub mod recovery_metrics;
pub mod recovery_session;
pub mod recovery_tracking;

pub mod advanced_waveform;
pub mod afsk_legacy;
pub mod anchors;
pub mod archive;
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
pub mod fec_sync;
pub mod formats;
pub mod generic;
pub mod geoscan;
/// Experimental predictive colored-noise sequence detector.
pub mod innovation;
pub mod innovation_audio;
pub mod input;
pub mod interference;
pub mod joint_sequence;
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
#[cfg(test)]
#[path = "tests/recovery_pipeline_tests.rs"]
mod recovery_pipeline_tests;
pub mod repetition;
/// Native research utilities: metrics, event grouping, split and packet audits.
pub mod research;
/// Opt-in robust likelihood experiment; not enabled by receiver defaults.
pub mod robust_sequence;
pub mod sequence;
pub mod soft;
/// Source-bound development replay of the optional soft sequence detector.
pub mod soft_audio;
/// Opt-in log-MAP sequence detector with explicit extrinsic information.
pub mod soft_sequence;
#[cfg(test)]
extern crate self as telemetry_yield_rs;
pub mod space_link;
pub mod space_packet_stream;
/// Opt-in development DSP; not used by the qualified receiver defaults.
pub mod tracking;
pub mod triage;
/// Experimental symbol-block BCJR/LDPC extrinsic iteration.
pub mod turbo;
/// CPU-only experimental predictive channel uncertainty; not in default paths.
pub mod uncertainty;
pub mod uncertainty_audio;
