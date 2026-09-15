//! Compare full native innovations results; strip only recorded timing fields.
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use telemetry_yield_rs::{input, protocol};
#[path = "support/native_result_json.rs"]
mod native_result_json;

#[derive(Parser)]
struct Args {
    #[arg(long)]
    left: PathBuf,
    #[arg(long)]
    right: PathBuf,
    #[arg(long)]
    output: PathBuf,
}

fn normalize(value: &mut Value) {
    match value {
        Value::Object(map) => {
            for key in [
                "elapsed_seconds",
                "total_wall_seconds",
                "stage_wall_seconds",
                "model_preparation_seconds",
                "new_lanes_seconds",
                "total_decode_seconds",
            ] {
                map.remove(key);
            }
            for child in map.values_mut() {
                normalize(child);
            }
        }
        Value::Array(items) => {
            for child in items {
                normalize(child);
            }
        }
        _ => {}
    }
}

fn validate(value: &Value) -> Result<(), String> {
    if !matches!(
        value["schema"].as_str(),
        Some("innovation-audio-result-v1" | "innovation-audio-result-v2")
    ) || value["sample_rate_hz"].as_u64().is_none_or(|n| n == 0)
        || value["samples"].as_u64().is_none_or(|n| n == 0)
        || value["config"]["baud"]
            .as_f64()
            .is_none_or(|n| !n.is_finite() || n <= 0.0)
        || value["config"]["threads"]
            .as_u64()
            .is_none_or(|n| !(1..=16).contains(&n))
        || !value["report"]["source_models"].is_array()
        || !value["report"]["trials"].is_array()
        || !value["report"]["baseline"].is_object()
    {
        return Err(
            "native report lacks required schema, geometry, configuration or trial metadata".into(),
        );
    }
    let expected: input::Identity =
        serde_json::from_value(value["source"].clone()).map_err(|e| e.to_string())?;
    let actual = input::identity(std::path::Path::new(&expected.path))?;
    if actual.sha256 != expected.sha256
        || actual.bytes != expected.bytes
        || actual.path != expected.path
    {
        return Err("native report source no longer matches its recorded identity".into());
    }
    let frames = value["report"]["union_full_frames"]
        .as_array()
        .ok_or("frame array absent")?;
    if value["status"] != "complete" || value["union_count"].as_u64() != Some(frames.len() as u64) {
        return Err("report is incomplete or count is inconsistent".into());
    }
    let mut seen = std::collections::BTreeSet::new();
    for frame in frames {
        let text = frame.as_str().ok_or("frame is not text")?;
        let bytes = hex::decode(text).map_err(|e| e.to_string())?;
        let mut crc = 0xffffu16;
        for &byte in &bytes {
            crc ^= u16::from(byte);
            for _ in 0..8 {
                crc = (crc >> 1) ^ if crc & 1 != 0 { 0x8408 } else { 0 };
            }
        }
        if bytes.len() < 18
            || crc != 0xf0b8
            || protocol::parse_ax25_ui(&bytes[..bytes.len() - 2]).is_none()
            || !seen.insert(text)
        {
            return Err("invalid or duplicate received FCS/UI frame".into());
        }
    }
    Ok(())
}

fn run(args: &Args) -> Result<Value, String> {
    let left_id = input::identity(&args.left)?;
    let right_id = input::identity(&args.right)?;
    let mut left = native_result_json::read_native_report(&args.left)?;
    let mut right = native_result_json::read_native_report(&args.right)?;
    validate(&left)?;
    validate(&right)?;
    normalize(&mut left);
    normalize(&mut right);
    let a = serde_json::to_vec(&left).map_err(|e| e.to_string())?;
    let b = serde_json::to_vec(&right).map_err(|e| e.to_string())?;
    if input::identity(&args.left)?.sha256 != left_id.sha256
        || input::identity(&args.right)?.sha256 != right_id.sha256
    {
        return Err("report changed during comparison".into());
    }
    Ok(json!({
        "schema":"innovation-report-parity-v1", "equal":a==b,
        "left":left_id,"right":right_id,
        "left_normalized_sha256":hex::encode(Sha256::digest(&a)),
        "right_normalized_sha256":hex::encode(Sha256::digest(&b)),
        "frames":left["union_count"],
        "source_models":left["report"]["source_models"].as_array().map(Vec::len),
        "trials":left["report"]["trials"].as_array().map(Vec::len),
        "same_input_geometry_config_and_all_deterministic_report_fields":a==b,
        "received_fcs_independently_checked":true,
        "normalization":"Only six named timing fields, including timing-only stage_wall_seconds; serde_json float_roundtrip preserves f64 values including signed zero. Executable identities belong to separately retained plans."
    }))
}

fn main() {
    let args = Args::parse();
    match run(&args).and_then(|value| {
        input::write_json_new(&args.output, &value)?;
        Ok(value)
    }) {
        Ok(value) => {
            println!("{value}");
            if value["equal"] != true {
                std::process::exit(1);
            }
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn empty_or_failed_reports_are_not_vacuous_equality_evidence() {
        assert!(
            validate(
                &json!({"status":"complete","union_count":0,"report":{"union_full_frames":[]}})
            )
            .is_err()
        );
        assert!(validate(&json!({"status":"failed"})).is_err());
    }
    #[test]
    fn normalization_preserves_payloads_numeric_details_signed_zero_and_trial_order() {
        let original = json!({"elapsed_seconds":2.0,"report":{"trials":[{"gain":0.75,"elapsed_seconds":4.0}],"x":-0.0,"frames":["abcd"]}});
        let mut normalized = original.clone();
        normalize(&mut normalized);
        let reference = serde_json::to_vec(&normalized).unwrap();
        for changed in [json!(0.0), json!(1e-30)] {
            let mut candidate = original.clone();
            candidate["report"]["x"] = changed;
            normalize(&mut candidate);
            assert_ne!(reference, serde_json::to_vec(&candidate).unwrap());
        }
        assert_eq!(normalized["report"]["trials"][0]["gain"], 0.75);
        assert_eq!(normalized["report"]["frames"], json!(["abcd"]));
    }
}
