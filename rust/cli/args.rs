//! Command-line schema. Parsing does not initialize the compute backend.

use clap::{Args, Parser, Subcommand};
use std::path::PathBuf;
use telemetry_yield_rs::{compat, compute, dsp, progressive, receiver};

#[derive(Parser)]
#[command(
    version,
    about = "Standalone deterministic parallel telemetry receiver; no Python runtime"
)]
pub(super) struct Cli {
    /// Optional FIR backend. Remaining decoder stages run on CPU in both modes.
    #[arg(long, global=true, value_enum, default_value_t=compute::Backend::Cpu)]
    pub(super) compute: compute::Backend,
    /// Sample-parallel CPU FIR workers, additional to the decoder's window workers.
    #[arg(long, global = true, default_value_t = 1)]
    pub(super) compute_threads: usize,
    #[arg(long, global = true, default_value_t = 0)]
    pub(super) cuda_device: usize,
    #[arg(long, global = true, default_value_t = 2)]
    pub(super) cuda_streams: usize,
    #[arg(long, global = true, default_value_t = 256)]
    pub(super) cuda_buffer_mib: usize,
    #[command(subcommand)]
    pub(super) command: Commands,
}

impl Cli {
    pub(super) fn compute_options(&self) -> compute::Options {
        compute::Options {
            backend: self.compute,
            cpu_threads: self.compute_threads,
            cuda_device: self.cuda_device,
            cuda_streams: self.cuda_streams,
            cuda_buffer_mib: self.cuda_buffer_mib,
        }
    }
}

#[derive(Args, Clone)]
pub(super) struct Settings {
    #[arg(long,default_value="fsk",value_parser=["fsk","afsk"])]
    pub(super) mode: String,
    #[arg(long, default_value_t = 9600.0)]
    pub(super) baud: f64,
    #[arg(long, default_value_t = 6.0)]
    pub(super) window_seconds: f64,
    #[arg(long, default_value_t = 3.0)]
    pub(super) hop_seconds: f64,
    #[arg(long, default_value_t = 1)]
    pub(super) threads: usize,
    #[arg(long,default_value="diverse",value_parser=["global","diverse","full","burst"])]
    pub(super) bank: String,
    #[arg(long, default_value_t = 16)]
    pub(super) top_timing: usize,
}
impl Settings {
    pub(super) fn config(&self) -> receiver::DecodeConfig {
        let dsp = dsp::DspConfig {
            mode: self.mode.clone(),
            baud: self.baud,
            bank: self.bank.clone(),
            top_timing: self.top_timing,
            ..Default::default()
        };
        receiver::DecodeConfig {
            dsp,
            window_seconds: self.window_seconds,
            hop_seconds: self.hop_seconds,
            threads: self.threads,
        }
    }
}

#[derive(Subcommand)]
pub(super) enum Commands {
    /// Probe selected backend, initialize it and report exact capabilities.
    ComputeInfo,
    /// Same-build end-to-end progressive CPU/backend benchmark with strict parity audit.
    BenchmarkComputePair {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long,value_enum,default_value_t=compute::Backend::Cuda)]
        candidate: compute::Backend,
        #[arg(long, default_value_t = 4)]
        candidate_threads: usize,
        #[arg(long, default_value_t = 4)]
        threads: usize,
        #[arg(long, default_value_t = 1)]
        repeats: usize,
    },
    /// Verify completed same-build progressive sessions down to task/model/frame bytes.
    AuditComputePair {
        #[arg(long)]
        cpu_session: PathBuf,
        #[arg(long)]
        candidate_session: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Bit-exact FIR qualification on synthetic cases and optional real WAV/OGG.
    /// This is not an end-to-end receiver publication benchmark.
    QualifyCompute {
        #[arg(long)]
        input: Option<PathBuf>,
        #[arg(long, default_value_t = 3)]
        repeats: usize,
        #[arg(long)]
        output: PathBuf,
    },
    /// Reassemble one Geoscan JPEG with explicit missing-byte coverage. KISS is external evidence only.
    AssembleGeoscan {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        kiss: bool,
    },
    /// Parse a complete CSP/AOS/USLP frame. stdin: {config, frame_hex}.
    ParseSpaceLink,
    /// Decode one explicit FEC word. stdin: {code, soft, decoded_bytes}.
    /// FEC convergence alone is NOT a validated telemetry packet.
    DecodeCodeword,
    /// Experimental Gaussian three-tap BCJR. stdin: {samples, channel, prior?}.
    DecodeSoftSequence,
    /// Experimental aligned symbol-block BCJR/LDPC iteration; requires CRC validator.
    /// Does not acquire or synchronize IQ/audio. See docs/soft-iterative-receiver.md.
    DecodeTurboBlock,
    /// Experimental PSK/(G)FSK/GMSK/AFSK IQ, pilot-estimated turbo/FEC, repeats and SIC.
    DecodeAdvancedIq {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        profile: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Variable-length AX.25 UI IQ recovery with an additive soft-sequence lane.
    DecodeRecoveryHdlc {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        profile: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Causal HDLC IQ channel reuse and optional signal-only blind bootstrap.
    DecodeRecoveryHdlcMemory {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        profile: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Immutable IQ window worklist; completed windows survive restart.
    DecodeRecoverySession {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        profile: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        resume: bool,
        /// Stop only between windows. This is not a wall-clock deadline.
        #[arg(long, default_value_t = 4096)]
        max_windows: usize,
    },
    /// Paired receipt accounting; does not certify provenance or publication readiness.
    SummarizeRecoveryStudy {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Development-only mono WAV/OGG BCJR/MLSE comparison, not a full progressive run.
    DecodeBcjrAudio {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 90.0)]
        duration_seconds: f64,
        #[arg(long, default_value_t = 9600.0)]
        baud: f64,
        #[arg(long, default_value_t = 1)]
        threads: usize,
    },
    /// Print an explicit named native FEC configuration (no waveform decoding).
    FecProfile {
        #[arg(long,value_parser=["ccsds-rs255-223","ccsds-tc128","ccsds-tc512"])]
        profile: String,
        #[arg(long, default_value_t = 1)]
        interleaving: usize,
        #[arg(long, default_value_t = 0)]
        shortening: usize,
    },
    /// Resumable mono-audio portfolio, supervised wall budgets including input preparation.
    DecodeProgressive {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long,default_value="quick",value_parser=["quick","deep","full"])]
        mode: String,
        #[arg(long)]
        budget_ms: Option<u64>,
        #[arg(long, default_value_t = 2)]
        threads: usize,
        /// Exact preparation cache per process in MiB (0 disables caching, not decoding).
        #[arg(long, default_value_t = progressive::DEFAULT_CACHE_MIB)]
        cache_mib: usize,
        /// Task ordering only; marginal-yield retains the complete bank and phase barriers.
        #[arg(long, value_enum, default_value_t = progressive::scheduler::Policy::Fixed)]
        scheduler: progressive::scheduler::Policy,
        #[arg(long, default_value_t = 9600.0)]
        baud: f64,
        #[arg(long)]
        resume: bool,
        #[arg(long)]
        no_blind: bool,
        #[arg(long)]
        no_multi_anchor: bool,
    },
    #[command(hide = true)]
    ProgressiveWorker {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        options: PathBuf,
        #[arg(long)]
        scratch: PathBuf,
        #[arg(long)]
        lock_fd: i32,
    },
    /// Exact old packaged AFSK search on one explicit IQ window; rejections stay separate.
    DecodeAfskLegacy {
        #[arg(long)]
        input: PathBuf,
        #[arg(long,value_parser=["ci16_le","cf32_le","cf64_le"])]
        format: String,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 0)]
        start_sample: usize,
        #[arg(long)]
        sample_count: usize,
    },
    /// Narrowband/CW inspection of CI16-LE; Morse text remains untrusted.
    InspectCw {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Candidate-only GNU HDLC compatibility. stdin: {bits:[0,1,...]}.
    CompatPdus {
        #[arg(long,value_parser=["decoded-bits","nrzi-g3ruh-levels"])]
        stage: String,
        #[arg(long, default_value_t = compat::GNU_HDLC_MAX_LENGTH)]
        max_length: usize,
    },
    /// Optional scheduling diagnostics only; never automatically prune decoder windows.
    InspectIq {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long,value_parser=["triage-ci16","route-ci16","burst-windows-ci16","phase-windows-cf32"])]
        operation: String,
        #[arg(long)]
        output: PathBuf,
    },
    /// Explicit external baseline adapter; candidates only, no network submission.
    BaselineGrSatellites {
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        segment: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        plan_only: bool,
    },
    /// Decode explicitly validated SigMF/raw manifest IQ with a metadata-format plan.
    DecodeMetadata {
        #[arg(long)]
        metadata: PathBuf,
        #[arg(long,value_parser=["sigmf","raw-manifest"])]
        kind: String,
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 1)]
        threads: usize,
        #[arg(long)]
        resume: bool,
    },
    /// Validate metadata, sample geometry and required checksums without decoding.
    InspectMetadata {
        #[arg(long)]
        metadata: PathBuf,
        #[arg(long,value_parser=["sigmf","raw-manifest"])]
        kind: String,
    },
    /// Parse transport records only; KISS presence is not telemetry validation.
    ParseKiss {
        #[arg(long)]
        input: PathBuf,
    },
    /// Data-only SatYAML catalogue inspection; no code execution or FEC inference.
    InspectSatyaml {
        #[arg(long,num_args=1..,required=true)]
        paths: Vec<PathBuf>,
    },
    /// Blind clipping-robust little-endian CI16 FSK, native frames + separate repair candidates.
    DecodeClippedCi16 {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        selected_windows: Option<PathBuf>,
        #[arg(long)]
        resume: bool,
        #[arg(long, default_value_t = 536870912)]
        maximum_scratch_bytes: u64,
    },
    /// Bounded bandlimited projection; output is reconstructed IQ, not original samples.
    DeclipCi16 {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        sample_rate_hz: f64,
        #[arg(long)]
        bandlimit_hz: f64,
        #[arg(long, default_value_t = 0)]
        start_sample: usize,
        #[arg(long)]
        sample_count: usize,
        #[arg(long, value_delimiter = ',', default_value = "1,4,12")]
        checkpoints: Vec<usize>,
    },
    /// Compare 1/N workers on identical loaded samples and complete outputs.
    BenchmarkAudio {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, value_delimiter = ',', default_value = "1,2,4")]
        workers: Vec<usize>,
        #[arg(long, default_value_t = 3)]
        repetitions: usize,
        #[arg(long)]
        first_seconds: Option<f64>,
        #[command(flatten)]
        settings: Settings,
    },
    /// Union already-validated detections. stdin: {detections:[...]}.
    CandidateLedger,
    /// Truth-isolated BPSK/FSK/QPSK/OQPSK physical bits, not validated telemetry.
    PhysicalDecode {
        #[arg(long)]
        input: PathBuf,
        #[arg(long,value_parser=["ci16_le","cf32_le","cf64_le"])]
        format: String,
        #[arg(long)]
        config: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 0)]
        start_sample: usize,
        #[arg(long)]
        sample_count: usize,
    },
    /// Explicit development BER scoring only. stdin {candidate,truth,policy,qpsk}.
    ScoreBits,
    /// Bounded soft repair. stdin {soft,threshold,budget,options}; output is candidates.
    SoftDecodeSymbols {
        #[arg(long,default_value="syndrome",value_parser=["syndrome","list"])]
        method: String,
    },
    /// Generic explicit IQ/audio waveform + protocol plan, no mission assumptions.
    Decode {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        plan: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 1)]
        threads: usize,
        #[arg(long)]
        resume: bool,
    },
    /// Experimental FSK/GMSK mono audio: four-path baseline plus disjoint-anchor MLSE.
    DecodeAdaptiveAudio {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        observation_id: Option<u64>,
        #[arg(long, default_value_t = 2)]
        threads: usize,
        #[arg(long, default_value_t = 9600.0)]
        baud: f64,
    },
    /// Decode mono WAV / OGG. OGG uses the same external FFmpeg as the frozen reference.
    DecodeAudio {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        observation_id: Option<u64>,
        #[command(flatten)]
        settings: Settings,
    },
    /// Replay existing local campaign audio. Reference frame bytes never enter decoding.
    BatchAudio {
        #[arg(long)]
        reference_summary: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        observation_id: Option<u64>,
        #[arg(long)]
        limit: Option<usize>,
        #[arg(long)]
        resume: bool,
        #[command(flatten)]
        settings: Settings,
    },
    /// Verify archive-absent candidate evidence and an existing full Rust replay.
    AuditArchiveCandidates {
        #[arg(long)]
        reference_summary: PathBuf,
        #[arg(long)]
        replay_root: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Strict full-cohort equivalence audit against immutable original evidence.
    CompareCampaign {
        #[arg(long)]
        reference_summary: PathBuf,
        #[arg(long)]
        results: PathBuf,
        #[arg(long)]
        output: Option<PathBuf>,
    },
    /// Clearly-labelled one-observation differential audit, not a full campaign pass.
    CompareObservation {
        #[arg(long)]
        reference_summary: PathBuf,
        #[arg(long)]
        observation_id: u64,
        #[arg(long)]
        result_dir: PathBuf,
        #[arg(long)]
        output: Option<PathBuf>,
    },
    /// Development oracle interface. stdin: {soft, threshold, g3ruh_modes}.
    DecodeAx25Symbols,
    /// Development numeric fixture interface. stdin: {pcm,sample_rate,config}.
    InspectDsp,
    /// Independent fixed-seed silence/Gaussian/tonal smoke tests; not FAR certification.
    NullSmoke {
        #[arg(long)]
        output: PathBuf,
        #[arg(long, default_value_t = 30)]
        per_kind: usize,
        #[command(flatten)]
        settings: Settings,
    },
    Capabilities,
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn command_schema_has_no_conflicting_flags() {
        Cli::command().debug_assert();
    }

    #[test]
    fn default_compute_is_cpu_without_backend_initialization() {
        let cli = Cli::try_parse_from(["receiver", "compute-info"]).unwrap();
        assert_eq!(cli.compute_options(), compute::Options::default());
    }

    #[test]
    fn global_compute_flags_work_on_either_side_of_the_subcommand() {
        for args in [
            vec![
                "receiver",
                "--compute",
                "cuda",
                "--cuda-streams",
                "3",
                "compute-info",
            ],
            vec![
                "receiver",
                "compute-info",
                "--compute",
                "cuda",
                "--cuda-streams",
                "3",
            ],
        ] {
            let cli = Cli::try_parse_from(args).unwrap();
            assert_eq!(cli.compute_options().backend, compute::Backend::Cuda);
            assert_eq!(cli.compute_options().cuda_streams, 3);
        }
    }

    #[test]
    fn invalid_backend_and_unknown_arguments_are_rejected() {
        assert!(Cli::try_parse_from(["receiver", "compute-info", "--compute", "magic"]).is_err());
        assert!(Cli::try_parse_from(["receiver", "compute-info", "--fastmath"]).is_err());
        let cli =
            Cli::try_parse_from(["receiver", "compute-info", "--compute-threads", "0"]).unwrap();
        assert!(cli.compute_options().validate().is_err());
    }
}
