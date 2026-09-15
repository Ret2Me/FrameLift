//! Typed original-OGG acquisition evidence. Hash consistency is not execution attestation.
use serde_json::{Value, json};
use std::{fs::File, io::Read, path::Path};
use telemetry_yield_rs::input;
fn verify(v: &Value) -> Result<(), String> {
    let path = Path::new(v["path"].as_str().ok_or("artifact path missing")?);
    if json!(input::identity(path)?) != *v {
        return Err(format!("download artifact changed: {}", path.display()));
    }
    Ok(())
}
fn same(a: &Value, b: &Value) -> bool {
    a["bytes"].is_u64()
        && a["sha256"].is_string()
        && a["bytes"] == b["bytes"]
        && a["sha256"] == b["sha256"]
}
fn success(v: &Value) -> bool {
    v["success"] == true && v["returncode"] == 0 && v["timed_out"] == false
}
pub fn validate(receipt: &Value, row: &Value) -> Result<(), String> {
    if receipt["schema"] != "innovation-original-ogg-download-v1"
        || receipt["status"] != "downloaded"
        || !row["id"].is_u64()
        || receipt["observation_id"] != row["id"]
        || !row["payload"].is_string()
        || receipt["source_url"] != row["payload"]
        || receipt["ogg_magic_valid"] != true
        || receipt["original_bytes"] != true
    {
        return Err("incomplete or relabelled original OGG receipt".into());
    }
    verify(&receipt["identity"])?;
    verify(&receipt["http_receipt"])?;
    let target = Path::new(receipt["identity"]["path"].as_str().unwrap());
    let mut magic = [0; 4];
    File::open(target)
        .and_then(|mut f| f.read_exact(&mut magic))
        .map_err(|e| e.to_string())?;
    if &magic != b"OggS" {
        return Err("original target has no Ogg magic".into());
    }
    let http = input::read_json(Path::new(receipt["http_receipt"]["path"].as_str().unwrap()))?;
    let download = &receipt["download"];
    if http["success"] != true
        || http["requested_url"] != row["payload"]
        || http["result"] != *download
        || download["url"] != row["payload"]
        || !same(&download["identity"], &receipt["identity"])
        || http["downloaded_bytes"]
            .as_u64()
            .is_none_or(|n| n > 64 * 1024 * 1024)
    {
        return Err("HTTP receipt/source mismatch".into());
    }
    verify(&download["identity"])?;
    let final_hop = http["hops"]
        .as_array()
        .and_then(|h| h.last())
        .ok_or("HTTP hops absent")?;
    if final_hop["http_status"] != "200"
        || !success(&final_hop["process"])
        || final_hop["url"] != download["final_url"]
    {
        return Err("final download not successful HTTP200".into());
    }
    for key in ["stdout", "stderr"] {
        verify(&final_hop["process"][key])?;
    }
    let metadata = &receipt["audio_metadata"];
    let duration = metadata["duration_seconds"]
        .as_f64()
        .ok_or("audio duration absent")?;
    let process = &metadata["probe"];
    let args = json!([
        "-v",
        "error",
        "-threads",
        "1",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        download["identity"]["path"]
    ]);
    if metadata["codec_name"] != "vorbis"
        || metadata["sample_rate"] != 48000
        || metadata["channels"] != 1
        || !duration.is_finite()
        || !(1.0..=1800.0).contains(&duration)
        || !success(process)
        || process["program"] != "/usr/bin/ffprobe"
        || process["args"] != args
    {
        return Err("audio probe contract/process mismatch".into());
    }
    verify(&metadata["probe_executable"])?;
    if metadata["probe_executable"]["path"] != "/usr/bin/ffprobe" {
        return Err("probe executable path mismatch".into());
    }
    for key in ["stdout", "stderr"] {
        verify(&process[key])?;
    }
    let probe = input::read_json(Path::new(process["stdout"]["path"].as_str().unwrap()))?;
    let streams = probe["streams"]
        .as_array()
        .filter(|s| s.len() == 1)
        .ok_or("probe requires one stream")?;
    if streams[0]["codec_name"] != "vorbis"
        || streams[0]["sample_rate"] != "48000"
        || streams[0]["channels"] != 1
        || probe["format"]["duration"]
            .as_str()
            .and_then(|s| s.parse::<f64>().ok())
            != Some(duration)
    {
        return Err("audio metadata differs from hashed probe output".into());
    }
    verify(&receipt["identity"])?;
    verify(&receipt["http_receipt"])?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identity_only_and_failed_receipts_are_not_success() {
        let row = json!({"id":1,"payload":"https://network.satnogs.org/test.ogg"});
        for v in [
            json!({}),
            json!({"identity":{}}),
            json!({"schema":"innovation-original-ogg-download-v1","status":"failed","observation_id":1}),
        ] {
            assert!(validate(&v, &row).is_err());
        }
    }
    #[test]
    fn process_success_is_not_exit_or_flag_alone() {
        assert!(success(
            &json!({"success":true,"returncode":0,"timed_out":false})
        ));
        for v in [
            json!({"success":true}),
            json!({"success":true,"returncode":1,"timed_out":false}),
            json!({"success":true,"returncode":0,"timed_out":true}),
        ] {
            assert!(!success(&v));
        }
    }
}
