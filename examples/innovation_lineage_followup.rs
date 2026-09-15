//! Hash-bound, deliberately partial review of historical exposure exceptions.
use chrono::DateTime;
use clap::Parser;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};
const SNAP: &str = "1d835ff644836ac9812c2a2f01887118f7fd485e6e4272693f916be6e4bca7b0";
const INDEX: &str = "da3d4f2c697b40b2c6d5fc9c730adef8052203830201c5cd9cbdf8a98b027ff9";
#[derive(Parser)]
struct Args {
    #[arg(long)]
    root: PathBuf,
    #[arg(long)]
    output: PathBuf,
}
fn sha(x: &[u8]) -> String {
    hex::encode(Sha256::digest(x))
}
fn read(path: &Path) -> Result<(Vec<u8>, Value), String> {
    let path = path.canonicalize().map_err(|e| e.to_string())?;
    let b = fs::read(&path).map_err(|e| e.to_string())?;
    let id = json!({"path":path,"bytes":b.len(),"sha256":sha(&b)});
    Ok((b, id))
}
fn index_rows(html: &str) -> Result<BTreeMap<u64, (String, String)>, String> {
    let mut out = BTreeMap::new();
    for row in html.split("<tr>") {
        let Some((_, tail)) = row.split_once("https://network.satnogs.org/observations/") else {
            continue;
        };
        let digits = tail
            .chars()
            .take_while(|c| c.is_ascii_digit())
            .collect::<String>();
        let Ok(id) = digits.parse::<u64>() else {
            continue;
        };
        let fields = row
            .split("<td>")
            .filter_map(|s| s.split_once("</td>").map(|x| x.0.trim()))
            .collect::<Vec<_>>();
        for (i, field) in fields.iter().enumerate() {
            if DateTime::parse_from_rfc3339(field).is_ok() {
                let name = fields
                    .get(i + 1)
                    .ok_or("index missing satellite")?
                    .to_string();
                let value = (field.to_string(), name);
                if out
                    .insert(id, value.clone())
                    .is_some_and(|old| old != value)
                {
                    return Err("index has conflicting observation metadata".into());
                }
                break;
            }
        }
    }
    Ok(out)
}
fn run(a: Args) -> Result<(), String> {
    let root = a.root.canonicalize().map_err(|e| e.to_string())?;
    let (b, snapshot) =
        read(&root.join("work/innovation-exposure-audit-20260911-v4/exposure-manifest.json"))?;
    if snapshot["sha256"] != SNAP {
        return Err("snapshot hash mismatch".into());
    }
    let manifest: Value = serde_json::from_slice(&b).map_err(|e| e.to_string())?;
    let (html, index) = read(&root.join("manifests/camras-index-2026-08-31.html"))?;
    if index["sha256"] != INDEX {
        return Err("CAMRAS index hash mismatch".into());
    }
    let indexed = index_rows(std::str::from_utf8(&html).map_err(|e| e.to_string())?)?;
    let cutoff = DateTime::parse_from_rfc3339("2026-08-11T00:00:00Z")
        .unwrap()
        .timestamp()
        - 7200;
    let mut controls = BTreeMap::new();
    let mut control_manifests = vec![];
    for dir in ["control-inputs", "control-inputs-v2"] {
        let (b, id) = read(&root.join(format!(
            "work/satnogs-holdout-20260908-v1/{dir}/manifest.json"
        )))?;
        let value: Value = serde_json::from_slice(&b).map_err(|e| e.to_string())?;
        if value["schema"] != "holdout-synthetic-controls-v1"
            || value["no_known_frames_inserted"] != true
        {
            return Err("unsupported control manifest".into());
        }
        for c in value["controls"].as_array().ok_or("missing controls")? {
            let path = c["input"]["path"]
                .as_str()
                .ok_or("control input path missing")?;
            controls.insert(path.to_string(), (c.clone(), id.clone()));
        }
        control_manifests.push(id);
    }
    let evidence = manifest["identified_exposure_evidence"]
        .as_array()
        .ok_or("evidence missing")?;
    let mut resolutions = vec![];
    let mut unresolved_keys = vec![];
    for k in manifest["unresolved_exposure_keys"]
        .as_array()
        .ok_or("keys missing")?
    {
        let key = k.as_str().ok_or("invalid key")?;
        let id = key
            .rsplit(':')
            .next()
            .ok_or("key invalid")?
            .parse::<u64>()
            .map_err(|e| e.to_string())?;
        if let Some((start, name)) = indexed.get(&id) {
            if !name.to_uppercase().contains("CANVAS")
                && DateTime::parse_from_rfc3339(start).unwrap().timestamp() < cutoff
            {
                resolutions.push(json!({"key":key,"resolution":"saved_index_identifies_other_satellite_before_target_window","start":start,"satellite_name":name,"source":index,"norad_not_invented":true}));
                continue;
            }
        }
        let matching = evidence
            .iter()
            .filter(|e| {
                e["keys"]
                    .as_array()
                    .is_some_and(|ks| ks.iter().any(|v| v == k))
            })
            .collect::<Vec<_>>();
        if [13371136, 6394603].contains(&id) && key.starts_with("public_satnogs:") {
            let (filename, expected_hash, description) = if id == 13371136 {
                (
                    "gr4-packet-modem-intelsat37e-test.sigmf-meta",
                    "5127e2a0f6e41ef9553049d492860a3dbbb44eb790141ec83536d7570c8c323c",
                    "Test of gr4-packet-modem through an Intelsat 37e C-band transponder",
                )
            } else {
                (
                    "GPS-L1-2022-03-27.sigmf-meta",
                    "b93f5a506fb9a916091b619dd0b979b634445b32f199e496f0143ece344fe5ca",
                    "Recording of GPS L1 signals",
                )
            };
            let dir = root.join(format!("work/zenodo/{id}"));
            if !matching.is_empty()
                && matching.iter().all(|e| {
                    e["path"]
                        .as_str()
                        .is_some_and(|p| Path::new(p).starts_with(&dir))
                })
            {
                let (bytes, source) = read(&dir.join(filename))?;
                if source["sha256"] != expected_hash {
                    return Err("known SigMF metadata changed".into());
                }
                let metadata: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                if metadata["global"]["core:description"] != description {
                    return Err("known SigMF description changed".into());
                }
                resolutions.push(json!({"key":key,"resolution":"Zenodo_record_number_not_public_SatNOGS_observation_identifier","source":source,"description":description,"capture_metadata":metadata["captures"],"known_non_CANVAS_recording":true}));
                continue;
            }
        }
        if id == 13422078 && matching.len() == 1 {
            let expected=root.join("work/single-run-weather-v1/supervisor-v3/runs/20260911T004133404246Z-13422078/result.json");
            if matching[0]["path"] == expected.to_str().unwrap() {
                let (bytes, source) = read(&expected)?;
                let value: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
                if value.get("active_http400_protocol_sha256").is_some()
                    && value.get("planned_batches").is_some()
                    && value.get("driver_policy").is_some()
                    && value.get("new_completed_batches_this_cycle").is_some()
                    && value.get("source").is_none()
                    && value.get("frames").is_none()
                {
                    resolutions.push(json!({"key":key,"resolution":"weather_metadata_supervisor_run_suffix_not_satellite_observation","source":source,"classification_basis":"HTTP400/batch/driver-policy operational status contract, no RF input or frame-result contract"}));
                    continue;
                }
            }
        }
        let mut receipts = vec![];
        let mut all_control = !matching.is_empty();
        for e in &matching {
            let path = Path::new(e["path"].as_str().ok_or("artifact path invalid")?);
            if path.extension().and_then(|x| x.to_str()) != Some("json") {
                all_control = false;
                break;
            }
            let (b, identity) = read(path)?;
            let v: Value = serde_json::from_slice(&b).map_err(|e| e.to_string())?;
            let Some((control, manifest)) =
                v["source"]["path"].as_str().and_then(|p| controls.get(p))
            else {
                all_control = false;
                break;
            };
            if v["schema"] != "tracking-audio-development-probe-v1"
                || v["observation_id"] != id
                || control["index"] != id
                || v["source"] != control["input"]
            {
                all_control = false;
                break;
            }
            let bound = e["lineage_contexts"]
                .as_array()
                .is_some_and(|xs| xs.iter().any(|x| x == &identity));
            if !bound {
                return Err("synthetic result changed from frozen lineage context".into());
            }
            receipts.push(json!({"decoder_artifact":identity,"control_manifest":manifest,"control":control,"waveform_bytes_reverified":false}));
        }
        if all_control {
            resolutions.push(json!({"key":key,"resolution":"local_synthetic_control_index_not_authenticated_private_observation_id","receipts":receipts,"limit":"matches declared generation recipe and decoder input identity, not new waveform content verification"}));
        } else {
            unresolved_keys.push(json!({"key":key,"exact_evidence_paths":matching.iter().map(|e|e["path"].clone()).collect::<Vec<_>>()}));
        }
    }
    let mut declared_synthetic = vec![];
    let mut unresolved_artifacts = vec![];
    for e in manifest["unlinked_artifacts"]
        .as_array()
        .ok_or("unlinked artifacts missing")?
    {
        if let Some((control, source)) = e["path"].as_str().and_then(|p| controls.get(p)) {
            declared_synthetic.push(json!({"artifact":e,"control":control,"manifest":source,"qualification":"declared recipe only; waveform bytes not reverified"}));
        } else {
            unresolved_artifacts.push(e.clone());
        }
    }
    let (_, source) = read(&root.join("examples/innovation_lineage_followup.rs"))?;
    let (_, exe) = read(&std::env::current_exe().map_err(|e| e.to_string())?)?;
    let report = json!({"schema":"innovation-lineage-review-v1","status":"partial","scope":"historical-local-exposure-reviewed","complete_review":false,
  "source":source,"executable":exe,"exposure_manifest":snapshot,"index":index,"control_manifests":control_manifests,
  "resolved_exception_count":resolutions.len(),"resolved_exceptions":resolutions,"unresolved_key_count":unresolved_keys.len(),"unresolved_keys_with_paths":unresolved_keys,
  "declared_synthetic_unlinked_artifact_count":declared_synthetic.len(),"declared_synthetic_unlinked_artifacts":declared_synthetic,
  "unresolved_unlinked_artifact_count":unresolved_artifacts.len(),"unresolved_unlinked_artifacts":unresolved_artifacts,
  "lineage_context_read_issues":manifest["lineage_read_issues"],"filesystem_coverage_issues":manifest["scan_issues"],
  "in_scope_blocking_findings":[{"code":"historical-lineage-coverage-not-closed","details":"Exact unresolved keys/artifacts and context/read/coverage exceptions retained in this receipt; no root-name-only synthetic waiver"},{"code":"non-atomic-snapshot-and-concurrent-exposure","details":"Need refreshed complete local exposure log before release"}],
  "current_exposure_check_passed":false,"evaluation_release_permitted":false,"fresh_independent_holdout_qualified":false,"global_absence_claimed":false,"waveform_bytes_read":0,"decoder_runs":0,"publication_ready":false});
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&a.output)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut f, &report).map_err(|e| e.to_string())?;
    f.write_all(b"\n")
        .and_then(|_| f.sync_all())
        .map_err(|e| e.to_string())?;
    println!(
        "partial lineage: resolved_keys={} unresolved_keys={} declared_synthetic_unlinked={} unresolved_artifacts={}; release=false",
        resolutions.len(),
        unresolved_keys.len(),
        declared_synthetic.len(),
        unresolved_artifacts.len()
    );
    Ok(())
}
fn main() {
    if let Err(e) = run(Args::parse()) {
        eprintln!("lineage follow-up failed: {e}");
        std::process::exit(1);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parses_only_matching_index_rows() {
        let html = "<tr><td><a href='https://network.satnogs.org/observations/12511021'>12511021</a></td><td>2025-10-06T09:07:43Z</td><td>RSP-03</td><td>good</td></tr>";
        assert_eq!(
            index_rows(html).unwrap()[&12511021],
            ("2025-10-06T09:07:43Z".into(), "RSP-03".into())
        );
        assert!(index_rows("<tr><td>1234</td></tr>").unwrap().is_empty());
    }
    #[test]
    fn rejects_conflicting_index_dates() {
        let a = "<tr><td><a href='https://network.satnogs.org/observations/12511021'>x</a></td><td>2025-10-06T09:07:43Z</td><td>RSP-03</td></tr>";
        assert!(index_rows(&format!("{}{}", a, a.replace("2025-10-06", "2025-10-07"))).is_err());
    }
}
