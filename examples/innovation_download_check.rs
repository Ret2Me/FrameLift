//! Offline typed-receipt integration control on an already downloaded development OGG.
#[allow(dead_code)]
#[path = "innovation_benchmark_acquire_v4.rs"]
mod acquisition;
#[path = "support/innovation_download_receipt.rs"]
mod receipt;
use serde_json::{Value, json};
use std::path::Path;
use telemetry_yield_rs::input;
fn run() -> Result<(), String> {
    let args = std::env::args().collect::<Vec<_>>();
    if args.len() != 4 {
        return Err(
            "LEGACY_DOWNLOADED_JSON COHORT_JSON NEW_OUTPUT_DIR required; never fetches".into(),
        );
    }
    let legacy_path = Path::new(&args[1]);
    let old = input::read_json(legacy_path)?;
    let cohort = input::read_json(Path::new(&args[2]))?;
    let row = cohort["observations"]
        .as_array()
        .ok_or("rows absent")?
        .iter()
        .find(|r| r["id"] == old["observation_id"])
        .ok_or("ID absent from cohort")?;
    if row["payload"] != old["download"]["url"] {
        return Err("legacy observation URL mismatch".into());
    }
    let out = Path::new(&args[3]);
    std::fs::create_dir(out).map_err(|e| e.to_string())?;
    let out = out.canonicalize().map_err(|e| e.to_string())?;
    let audio = acquisition::verify_audio(
        Path::new(
            old["download"]["identity"]["path"]
                .as_str()
                .ok_or("source absent")?,
        ),
        &out,
    )?;
    let mut upgraded = old.clone();
    upgraded["schema"] = json!("innovation-original-ogg-download-v1");
    upgraded["status"] = json!("downloaded");
    upgraded["source_url"] = row["payload"].clone();
    upgraded["original_bytes"] = json!(true);
    upgraded["audio_metadata"] = audio;
    upgraded["offline_revalidation_only_no_download"] = json!(true);
    upgraded["legacy_receipt"] = json!(input::identity(legacy_path)?);
    upgraded["revalidated_utc"] = json!(acquisition::io::utc());
    receipt::validate(&upgraded, row)?;
    let mutations = [
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
    ];
    let mut rejected = vec![];
    for (key, value) in mutations {
        let mut bad = upgraded.clone();
        bad[key] = value;
        if receipt::validate(&bad, row).is_ok() {
            return Err(format!("malformed {key} accepted"));
        }
        rejected.push(key);
    }
    let mut bad = upgraded.clone();
    bad["audio_metadata"]["probe"]["timed_out"] = json!(true);
    if receipt::validate(&bad, row).is_ok() {
        return Err("timed-out probe accepted".into());
    }
    rejected.push("probe_timeout");
    input::write_json_new(&out.join("revalidated-receipt.json"), &upgraded)?;
    let report = json!({"schema":"typed-download-offline-integration-v1","status":"pass","legacy_receipt":input::identity(legacy_path)?,"cohort":input::identity(Path::new(&args[2]))?,"revalidated_receipt":input::identity(&out.join("revalidated-receipt.json"))?,"executable":input::identity(&std::env::current_exe().map_err(|e|e.to_string())?)?,"valid_actual_source_and_original_http_receipt":true,"fresh_ffprobe_executed":true,"rejected_mutations":rejected,"new_waveform_fetches":0,"decoder_runs":0,"not_independent_scientific_validation":true});
    input::write_json_new(&out.join("result.json"), &report)
}
fn main() {
    if let Err(e) = run() {
        eprintln!("offline receipt check failed: {e}");
        std::process::exit(1);
    }
}
