//! Explicit refetch of one ALREADY EXPOSED development recording for transport tests.
//! Not admission or acquisition of any new historical/prospective benchmark candidate.
#[allow(dead_code)]
#[path = "innovation_benchmark_acquire_v4.rs"]
mod acquisition;
#[path = "support/innovation_download_receipt.rs"]
mod receipt;
use serde_json::json;
use std::path::Path;
use telemetry_yield_rs::input;
const SOURCE_SHA: &str = "db9c4f43f5d707847191b6a1ad9e44d99a4a104ef3234767f401c2707e61bd9a";
const URL: &str = "https://network-satnogs.freetls.fastly.net/media/data_obs/2026/9/10/8/14967362/satnogs_14967362_2026-09-10T08-22-25.ogg";
fn run() -> Result<(), String> {
    let args = std::env::args().collect::<Vec<_>>();
    if args.len() != 2 {
        return Err("NEW_OUTPUT_DIR required; fixed previously exposed obs14967362 only".into());
    }
    let out = Path::new(&args[1]);
    std::fs::create_dir(out).map_err(|e| e.to_string())?;
    let out = out.canonicalize().map_err(|e| e.to_string())?;
    input::write_json_new(
        &out.join("plan.json"),
        &json!({"schema":"known-development-transport-control-v1","created_utc":acquisition::io::utc(),"observation_id":14967362,"source_url":URL,"expected_sha256":SOURCE_SHA,"not_independent_data":true,"candidate_cohort_downloads":0,"decoder_runs":0}),
    )?;
    let target = out.join("capture.ogg");
    let download = acquisition::io::fetch(URL, &target, 64 * 1024 * 1024)?;
    let identity = input::identity(&target)?;
    if identity.sha256 != SOURCE_SHA {
        return Err("refetched development source changed; cannot call this the same input".into());
    }
    let audio = acquisition::verify_audio(&target, &out)?;
    let row = json!({"id":14967362,"payload":URL});
    let value = json!({"schema":"innovation-original-ogg-download-v1","status":"downloaded","source_url":URL,"observation_id":14967362,"completed_utc":acquisition::io::utc(),"identity":identity,"audio_metadata":audio,"download":download,"ogg_magic_valid":true,"original_bytes":true,"http_receipt":input::identity(&out.join("capture.ogg.http.json"))?,"known_development_transport_test_not_cohort_admission":true});
    receipt::validate(&value, &row)?;
    let mut rejected = vec![];
    for (key, bad) in [
        ("schema", json!("wrong")),
        ("status", json!("incomplete")),
        ("observation_id", json!(0)),
        ("source_url", json!("https://example.invalid/wrong.ogg")),
        ("ogg_magic_valid", json!(false)),
        ("original_bytes", json!(false)),
        ("audio_metadata", json!({})),
        ("http_receipt", json!({})),
        ("identity", json!({})),
        ("download", json!({})),
    ] {
        let mut changed = value.clone();
        changed[key] = bad;
        if receipt::validate(&changed, &row).is_ok() {
            return Err(format!("malformed {key} accepted"));
        }
        rejected.push(key);
    }
    let mut changed = value.clone();
    changed["audio_metadata"]["probe"]["timed_out"] = json!(true);
    if receipt::validate(&changed, &row).is_ok() {
        return Err("timedout probe accepted".into());
    }
    rejected.push("probe_timeout");
    input::write_json_new(&out.join("downloaded.json"), &value)?;
    let reread = input::read_json(&out.join("downloaded.json"))?;
    receipt::validate(&reread, &row)?;
    input::write_json_new(
        &out.join("result.json"),
        &json!({"schema":"known-development-transport-check-v1","status":"pass","input":input::identity(&target)?,"receipt":input::identity(&out.join("downloaded.json"))?,"executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"positive_receipt_passes_initial_and_reread":true,"rejected_mutations":rejected,"known_development_refetches":1,"new_candidate_waveform_fetches":0,"decoder_runs":0,"publication_ready":false}),
    )
}
fn main() {
    if let Err(e) = run() {
        eprintln!("known development transport check failed: {e}");
        std::process::exit(1);
    }
}
