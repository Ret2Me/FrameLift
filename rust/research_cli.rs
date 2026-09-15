//! Offline native tools for dataset and packet audits.

use clap::{Parser, Subcommand};
use serde::Deserialize;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use telemetry_yield_rs::research::{
    self, config::EffectiveConfig, events, metrics, pcap, selection, store::AttemptStore,
};

const MAX_JSON_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Parser)]
#[command(
    name = "framelift-research",
    about = "Offline research audits; no Python or network access"
)]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Reserve, finish or inspect attempts in the legacy-compatible SQLite store.
    Attempts {
        #[arg(long)]
        database: PathBuf,
        #[arg(long)]
        input: PathBuf,
    },
    /// Fingerprint every effective decoder field using canonical JSON.
    ConfigHash {
        #[arg(long)]
        input: PathBuf,
    },
    /// Derive the outcome-blind selection salt from preregistration and beacon output.
    DeriveSelectionSalt {
        #[arg(long)]
        input: PathBuf,
    },
    /// Check pulse metadata and certificate binding, NOT the pulse signature.
    ValidateBeacon {
        #[arg(long)]
        input: PathBuf,
    },
    /// Select a deterministic, diversity-capped cohort without decoder outcomes.
    SelectCohort {
        #[arg(long)]
        input: PathBuf,
    },
    /// Count event-local distinct frames and optional CPU/negative-exposure rates.
    Metrics {
        #[arg(long)]
        input: PathBuf,
    },
    /// Reject an event present in more than one dataset split.
    CheckSplits {
        #[arg(long)]
        input: PathBuf,
    },
    /// Match events only with identity, time and physical evidence.
    SameEvent {
        #[arg(long)]
        input: PathBuf,
    },
    /// Check nonempty, verified and publication-reviewed provenance.
    ExportGate {
        #[arg(long)]
        input: PathBuf,
    },
    /// Independently check IPv4 and ICMP checksums in classic PCAP.
    AuditPcap {
        #[arg(long)]
        input: PathBuf,
    },
    /// Audit IPv4 PDUs with big-endian uint32 length prefixes.
    AuditIpv4Pdus {
        #[arg(long)]
        input: PathBuf,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SaltInput {
    domain: String,
    preregistration_sha256: String,
    output_value: String,
}

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
enum AttemptInput {
    Reserve {
        recording_id: String,
        config_hash: String,
        config: serde_json::Map<String, Value>,
        stale_after_seconds: u64,
    },
    Finish {
        recording_id: String,
        config_hash: String,
        status: String,
        result: serde_json::Map<String, Value>,
        lease_token: String,
    },
    Get {
        recording_id: String,
        config_hash: String,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BeaconInput {
    document: Value,
    expected_timestamp: String,
    certificate_pem: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CohortInput {
    catalogue: Value,
    download_plan: Value,
    eligible_modes: Vec<String>,
    development_exclusions: Vec<u64>,
    selection_salt_hex: String,
    target_observations: usize,
    maximum_per_satellite: usize,
    minimum_satellites: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NegativeExposure {
    n_frames_on_negative: u64,
    total_hours_negative: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MetricsInput {
    frames: Vec<metrics::FrameRecord>,
    cpu_seconds: Option<f64>,
    negative: Option<NegativeExposure>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EventInput {
    left: events::EventCandidate,
    right: events::EventCandidate,
    #[serde(default = "default_tolerance")]
    time_tolerance_seconds: i64,
    #[serde(default)]
    doppler_tracks_consistent: bool,
}

fn default_tolerance() -> i64 {
    30
}

fn read_json<T: DeserializeOwned>(path: &Path) -> Result<T, String> {
    let bytes = research::input::read_regular_bounded(path, MAX_JSON_BYTES)?;
    serde_json::from_slice(&bytes).map_err(|e| format!("invalid input: {e}"))
}

fn execute(command: Command) -> Result<Value, String> {
    match command {
        Command::Attempts { database, input } => {
            let request: AttemptInput = read_json(&input)?;
            let result = match request {
                AttemptInput::Reserve {
                    recording_id,
                    config_hash,
                    config,
                    stale_after_seconds,
                } => {
                    let token = AttemptStore::open(&database)?.reserve(
                        &recording_id,
                        &config_hash,
                        &config,
                        std::time::Duration::from_secs(stale_after_seconds),
                    )?;
                    json!({"reserved":token.is_some(),"lease_token":token})
                }
                AttemptInput::Finish {
                    recording_id,
                    config_hash,
                    status,
                    result,
                    lease_token,
                } => {
                    AttemptStore::open_existing(&database)?.finish(
                        &recording_id,
                        &config_hash,
                        &status,
                        &result,
                        &lease_token,
                    )?;
                    json!({"finished":true})
                }
                AttemptInput::Get {
                    recording_id,
                    config_hash,
                } => {
                    json!({"attempt":AttemptStore::inspect(&database, &recording_id, &config_hash)?})
                }
            };
            Ok(json!({"schema_version":"framelift-attempt-store-v1","result":result}))
        }
        Command::ConfigHash { input } => {
            let request: EffectiveConfig = read_json(&input)?;
            Ok(
                json!({"schema_version":"framelift-config-hash-v1","config_hash":request.fingerprint()?}),
            )
        }
        Command::DeriveSelectionSalt { input } => {
            let request: SaltInput = read_json(&input)?;
            let salt = selection::derive_selection_salt(
                &request.domain,
                &request.preregistration_sha256,
                &request.output_value,
            )?;
            Ok(
                json!({"schema_version":"framelift-selection-salt-v1","selection_salt_hex":hex::encode(salt)}),
            )
        }
        Command::ValidateBeacon { input } => {
            let request: BeaconInput = read_json(&input)?;
            selection::validate_nist_beacon_pulse(
                &request.document,
                &request.expected_timestamp,
                &request.certificate_pem,
            )
        }
        Command::SelectCohort { input } => {
            let request: CohortInput = read_json(&input)?;
            let salt: [u8; 32] = hex::decode(&request.selection_salt_hex)
                .map_err(|_| "selection_salt_hex must be hexadecimal")?
                .try_into()
                .map_err(|_| "selection salt must contain 32 bytes")?;
            let pool = selection::build_eligible_pool(
                &request.catalogue,
                &request.download_plan,
                &request.eligible_modes,
                &request.development_exclusions,
            )?;
            let selected = selection::select_public_random_cohort(
                &pool,
                &salt,
                request.target_observations,
                request.maximum_per_satellite,
                request.minimum_satellites,
            )?;
            Ok(
                json!({"schema_version":"framelift-cohort-selection-v1", "eligible_observations":pool.len(),"selection_salt_hex":hex::encode(salt),"selected":selected}),
            )
        }
        Command::Metrics { input } => {
            let request: MetricsInput = read_json(&input)?;
            let keys = metrics::unique_crc_frame_keys(&request.frames)?;
            let rate = request
                .cpu_seconds
                .map(|seconds| metrics::frames_per_cpu_second(keys.len() as u64, seconds))
                .transpose()?;
            let false_accepts = request
                .negative
                .map(|negative| {
                    metrics::false_accepts_per_hour(
                        negative.n_frames_on_negative,
                        negative.total_hours_negative,
                    )
                })
                .transpose()?;
            Ok(
                json!({"schema_version":"framelift-yield-metrics-v1","unique_crc_frames":keys.len(),"unique_crc_frame_keys":keys,
                "frames_per_cpu_second":rate,"false_accepts_per_hour":false_accepts,
                "input_crc_flags_independently_verified":false}),
            )
        }
        Command::CheckSplits { input } => {
            let rows: Vec<(String, String)> = read_json(&input)?;
            research::assert_no_event_leakage(&rows)?;
            Ok(
                json!({"schema_version":"framelift-split-audit-v1","rows":rows.len(),"event_leakage":false}),
            )
        }
        Command::SameEvent { input } => {
            let request: EventInput = read_json(&input)?;
            let tolerance = chrono::Duration::try_seconds(request.time_tolerance_seconds)
                .ok_or("time tolerance overflow")?;
            let same = events::same_transmission_event(
                &request.left,
                &request.right,
                tolerance,
                request.doppler_tracks_consistent,
            )?;
            Ok(
                json!({"schema_version":"framelift-event-match-v1","same_transmission_event":same,"physical_evidence_independently_verified":false}),
            )
        }
        Command::ExportGate { input } => {
            let records: Vec<research::LicenseMetadata> = read_json(&input)?;
            research::assert_export_allowed(&records)?;
            Ok(
                json!({"schema_version":"framelift-export-gate-v1","export_allowed":true,"source_records":records.len()}),
            )
        }
        Command::AuditPcap { input } => pcap::audit_pcap_echo_requests(&input),
        Command::AuditIpv4Pdus { input } => pcap::audit_length_prefixed_echo_requests(&input),
    }
}

fn run() -> Result<(), String> {
    let result = execute(Args::parse().command)?;
    let mut stdout = std::io::stdout().lock();
    serde_json::to_writer(&mut stdout, &result).map_err(|e| e.to_string())?;
    stdout.write_all(b"\n").map_err(|e| e.to_string())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("framelift-research: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strict_json_types_reject_boolean_counts_and_duplicate_fields() {
        for text in [
            r#"{"frames":[],"cpu_seconds":1,"cpu_seconds":2}"#,
            r#"{"frames":[],"negative":{"n_frames_on_negative":true,"total_hours_negative":1}}"#,
            r#"{"frames":[],"negative":{"n_frames_on_negative":-1,"total_hours_negative":1}}"#,
            r#"{"frames":[],"cpu_seconds":1,"ignored_option":true}"#,
        ] {
            assert!(serde_json::from_str::<MetricsInput>(text).is_err());
        }
    }

    #[test]
    fn malformed_and_oversized_json_fail_without_partial_results() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("input.json");
        std::fs::write(&path, b"[").unwrap();
        assert!(read_json::<Value>(&path).is_err());
        std::fs::File::options()
            .write(true)
            .open(&path)
            .unwrap()
            .set_len(MAX_JSON_BYTES + 1)
            .unwrap();
        assert!(read_json::<Value>(&path).unwrap_err().contains("bound"));
        assert!(read_json::<Value>(dir.path()).is_err());
    }
}
