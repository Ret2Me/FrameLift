//! Register a prospective CANVAS metadata selection before its observation window.
//! No network access, waveform reads, decoding or release admission is implemented.
#![recursion_limit = "512"]
use chrono::{DateTime, Utc};
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

const START: &str = "2026-09-12T00:00:00Z";
const END: &str = "2026-09-19T00:00:00Z";
const GUARD: i64 = 7200;
const NATIVE_SHA: &str = "e59e8dac6991984de1b7b1e7238835dd3ad740150ae5004070c215290ee2fda3";
const IMPROVED_SHA: &str = "638066b9c3edee6227373aa7919c4ee8d9705ca4ad89426f30398987b6e5042a";
const BUILD_SHA: &str = "6f738b48882bc1e7b9e49b3e643455ad853c0ef757774b4832956dc861118e19";
const TX: &str = "GCmN6RULea8dAT7Qoat8z2";
#[derive(Parser)]
struct Args {
    #[arg(long)]
    root: PathBuf,
    #[arg(long)]
    output: PathBuf,
}
fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn identity(path: &Path) -> Result<Value, String> {
    let path = path.canonicalize().map_err(|e| e.to_string())?;
    let bytes = fs::read(&path).map_err(|e| e.to_string())?;
    Ok(json!({"path":path,"bytes":bytes.len(),"sha256":digest(&bytes)}))
}
fn pinned(path: &Path, expected: &str) -> Result<Value, String> {
    let id = identity(path)?;
    if id["sha256"] != expected {
        return Err(format!("hash mismatch: {}", path.display()));
    }
    Ok(id)
}
fn time(s: &str) -> Result<i64, String> {
    DateTime::parse_from_rfc3339(s)
        .map(|v| v.timestamp())
        .map_err(|e| e.to_string())
}
fn registration_time_ok(now: i64) -> Result<(), String> {
    if now + GUARD >= time(START)? {
        return Err("registration too late for frozen future window; requires explicit new version, never silently shift window".into());
    }
    Ok(())
}
fn write_new(path: &Path, value: &Value) -> Result<(), String> {
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut f, value).map_err(|e| e.to_string())?;
    f.write_all(b"\n")
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())
}

// Pure metadata predicate, reusable by a future acquisition implementation.
// Deliberately never looks at status, waterfall, demoddata, payload contents or yield.
fn eligible(row: &Value) -> Result<bool, String> {
    let start = time(row["start"].as_str().ok_or("missing start")?)?;
    let end = time(row["end"].as_str().ok_or("missing end")?)?;
    if start < time(START)? || start >= time(END)? || end <= start || end > time(END)? {
        return Ok(false);
    }
    Ok(row["id"].as_u64().is_some_and(|v| v > 0)
        && row["ground_station"].as_u64().is_some_and(|v| v > 0)
        && row["norad_cat_id"] == 68635
        && row["transmitter_mode"] == "GMSK"
        && row["transmitter_baud"].as_f64() == Some(9600.0)
        && row["transmitter_uuid"] == TX
        && row["transmitter"] == TX
        && row["payload"]
            .as_str()
            .is_some_and(|v| v.starts_with("https://")))
}
fn run(args: Args) -> Result<(), String> {
    let root = args.root.canonicalize().map_err(|e| e.to_string())?;
    let epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_secs() as i64;
    registration_time_ok(epoch)?;
    let registered = DateTime::<Utc>::from_timestamp(epoch, 0)
        .ok_or("invalid clock")?
        .to_rfc3339();
    let native = pinned(
        &root.join("work/decoder-runtime-repair-20260911-v1/bin/telemetry-yield-rs"),
        NATIVE_SHA,
    )?;
    let improved = pinned(
        &root.join("work/decoder-runtime-repair-20260911-v1/bin/innovation_audio_probe"),
        IMPROVED_SHA,
    )?;
    let build = pinned(
        &root.join("work/decoder-runtime-repair-20260911-v1/build-and-tests.json"),
        BUILD_SHA,
    )?;
    let source = identity(&root.join("examples/innovation_prospective_register.rs"))?;
    let executable = identity(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    fs::create_dir(&args.output).map_err(|e| e.to_string())?;
    let out = args.output.canonicalize().map_err(|e| e.to_string())?;
    let method = json!({"schema":"innovation-prospective-method-freeze-v1","status":"registered","frozen_utc":registered,
  "progressive":native,"improved":improved,"runtime_repair_build":build,"registration_source":source,"registration_executable":executable,
  "algorithm_changes_after_freeze_require_new_protocol_and_new_future_window":true,
  "full_benchmark_runner_and_comparator_freeze_still_required":true,
  "novel_modulation_or_novel_clock_algorithm_claimed":false,"deployment_ready":false,"publication_ready":false});
    write_new(&out.join("method-freeze.json"), &method)?;
    let method_id = identity(&out.join("method-freeze.json"))?;
    let protocol = json!({"schema":"innovation-prospective-selection-v1","status":"registered_not_acquired","registered_utc":registered,"method_freeze":method_id,
  "observation_start_inclusive_utc":START,"observation_start_exclusive_utc":END,"observation_end_must_not_exceed_utc":END,
  "registration_guard_seconds":GUARD,"require_full_receiver_freeze_before_window_start_minus_guard":true,
  "duration_days":7,"target_maximum":500,"no_minimum_or_success_stopping_rule":true,
  "if_fewer_than_500_eligible":"retain all eligible; report shortfall; never extend dates or replace based on decoding yield",
  "mission":"CANVAS","norad_cat_id":68635,"transmitter_uuid":TX,"mode":"GMSK","baud":9600,
  "published_metadata_required":{"transmitter":"exact UUID match","transmitter_uuid":"exact UUID match","transmitter_mode":"GMSK","transmitter_baud":9600,"ground_station":"positive integer","payload":"HTTPS URL present"},
  "protocol_scope":"Known AX.25/G3RUH profile; no universal-modulation or all-mission claim",
  "do_not_filter_on":["status","waterfall_status","demoddata","decoded_frame_count","decode_status","signal_visibility","recovered_frames","comparative_yield"],
  "metadata_query":{"host":"network.satnogs.org","path":"/api/observations/","filters":{"satellite__norad_cat_id":68635,"start__gte":START,"start__lt":END},"outcome_filters":[]},
  "metadata_pagination":{"require_true_eof":true,"max_pages":300,"cap_is_not_eof":true,"preserve_requested_final_and_each_hop_url_filters":true,"require_final_http_200":true,"honor_retry_after":true,"hash_receipts_headers_bodies":true,"no_prefix_selection":true},
  "metadata_freeze_timing":"after complete fixed observation window and true EOF; metadata-only monitoring may occur earlier but cannot commit final cohort or select on partial availability",
  "selection":{"schema":"station-day-round-robin-v1","group_key":"ground_station + UTC start date","group_rank":"SHA256('innovation-prospective-canvas-v1-group:' + group_key), ascending hexadecimal then group_key","row_rank":"SHA256('innovation-prospective-canvas-v1-row:' + decimal observation id), ascending hexadecimal then numeric id","procedure":"within each group sort rows; sort groups; round-robin take one next row from every nonempty group until target maximum or exhaustion","seed_changes_forbidden":true,"no_replacements_after_waveform_access":true},
  "prior_exposure":{"same_norad_closed_interval_gap_seconds_lte":GUARD,"station_and_transmitter_ignored_for_guard":true,"metadata_only_not_waveform_exposure":true,"all_local_waveform_and_decoder_access_must_be_logged_from_registration":true,"known_prior_input_or_same_pass_excludes_candidate_before_waveform_acquisition":true,"concurrent_in_scope_development_invalidates_release":true,"unresolved_in_scope_lineage_blocks_release":true,"global_absence_claimed":false,"orbit_propagation_claimed":false},
  "within_evaluation_pass_dependence":"Retain same-pass observations as paired recordings, assign same-NORAD connected components under120min gap for clustered inference; never count recordings from one pass as independent satellite opportunities",
  "input_contract":{"codec":"vorbis","channels":1,"sample_rate_hz":48000,"duration_seconds_inclusive":[1,1800],"convert_once_to_hash_bound_shared_PCM16_for_every_arm":true,"OGG_is_not_raw_IQ":true},
  "missing_audio_policy":"keep selected IDs in intended denominator; record unavailable/invalid separately; no yield-dependent replacements",
  "resource_limits":{"metadata_page_bytes":4194304,"audio_per_file_bytes":67108864,"audio_total_transfer_bytes":12884901888u64,"concurrent_downloads":4,"minimum_disk_reserve_gib":25},
  "benchmark_operational_freeze":{"workers":4,"threads":2,"decoder_seconds":900,"arms":["innovation_v2","innovation_v1_no_codec","progressive_v3","direwolf","gr_satellites"],"runner_comparators_profile_and_legacy_arm_identity_pending":true,"all_execution_and_analysis_parameters_must_be_frozen_before_first_eligible_start_minus_guard":true},
  "analysis_registration_required_before_window":"Primary endpoint, candidate/baseline frame-set definitions, paired/clustered uncertainty procedure, controls and failure handling must be fixed in a separate hash-bound analysis protocol before the window; this selection registration alone does not assert that is done",
  "release_contract":"innovation-waveform-release-v1","cohort_schema":"innovation-benchmark-cohort-v2","new_source_cohort_required":true,"old_historical500_not_reused_as_confirmatory":true,
  "current_exposure_check_passed":false,"evaluation_release_permitted":false,"fresh_independent_holdout_qualified":false,"publication_ready":false,"waveform_downloads":0,"decoder_runs":0});
    write_new(&out.join("protocol.json"), &protocol)?;
    let protocol_id = identity(&out.join("protocol.json"))?;
    write_new(
        &out.join("registration-receipt.json"),
        &json!({"schema":"innovation-prospective-registration-receipt-v1","status":"registered_not_acquired","registered_utc":registered,"method_freeze":method_id,"protocol":protocol_id,"registration_source":source,"registration_executable":executable,"receiver_bytes_reverified":true,"observation_window_strictly_future":true,"evaluation_release_permitted":false,"complete_evaluation_release":false,"publication_ready":false,"waveform_downloads":0,"decoder_runs":0}),
    )?;
    println!("prospective registration complete; window={START}..{END}; acquisition_release=false");
    Ok(())
}
fn main() {
    if let Err(error) = run(Args::parse()) {
        eprintln!("prospective registration failed: {error}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn row() -> Value {
        json!({"id":15123456,"ground_station":41,"norad_cat_id":68635,"transmitter":TX,"transmitter_uuid":TX,"transmitter_mode":"GMSK","transmitter_baud":9600.0,"start":"2026-09-12T00:00:00Z","end":"2026-09-12T00:10:00Z","payload":"https://example.invalid/capture.ogg"})
    }
    #[test]
    fn registration_must_precede_guard() {
        assert!(registration_time_ok(time(START).unwrap() - GUARD - 1).is_ok());
        assert!(registration_time_ok(time(START).unwrap() - GUARD).is_err());
        assert!(registration_time_ok(time(END).unwrap()).is_err());
    }
    #[test]
    fn inclusive_start_exclusive_end() {
        assert!(eligible(&row()).unwrap());
        let mut r = row();
        r["start"] = "2026-09-11T23:59:59Z".into();
        assert!(!eligible(&r).unwrap());
        r["start"] = END.into();
        r["end"] = "2026-09-19T00:10:00Z".into();
        assert!(!eligible(&r).unwrap());
    }
    #[test]
    fn outcomes_do_not_change_eligibility() {
        let a = row();
        let mut b = a.clone();
        b["status"] = "bad".into();
        b["demoddata"] = json!([{"fake_frame":"not read"}]);
        b["waterfall_status"] = "good".into();
        assert_eq!(eligible(&a), eligible(&b));
    }
    #[test]
    fn metadata_profile_is_exact() {
        for (key, value) in [
            ("transmitter_mode", json!("BPSK")),
            ("transmitter_baud", json!(4800)),
            ("transmitter_uuid", json!("other")),
            ("norad_cat_id", json!(42)),
            ("ground_station", Value::Null),
        ] {
            let mut r = row();
            r[key] = value;
            assert!(!eligible(&r).unwrap());
        }
    }
    #[test]
    fn invalid_time_is_error_and_zero_duration_ineligible() {
        let mut r = row();
        r["start"] = "bad".into();
        assert!(eligible(&r).is_err());
        r["start"] = r["end"].clone();
        assert!(!eligible(&r).unwrap());
    }
}
