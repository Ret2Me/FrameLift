//! Conservative local exposure inventory; never opens waveforms or secrets.
use super::{cohort::Exposure, read};
use crate::input;
use serde_json::{Value, json};
use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

fn path_ids(text: &str, ids: &mut BTreeSet<u64>) {
    for part in text.split(|c: char| !c.is_ascii_digit()) {
        if (7..=8).contains(&part.len())
            && let Ok(id) = part.parse::<u64>()
        {
            ids.insert(id);
        }
    }
}

fn value_ids(value: &Value, ids: &mut BTreeSet<u64>) {
    match value {
        Value::Object(map) => {
            for (key, item) in map {
                if ["id", "observation_id", "observation"].contains(&key.as_str())
                    && let Some(id) = item
                        .as_u64()
                        .filter(|v| (1_000_000..100_000_000).contains(v))
                {
                    ids.insert(id);
                }
                value_ids(item, ids);
            }
        }
        Value::Array(items) => {
            for item in items {
                value_ids(item, ids);
            }
        }
        _ => {}
    }
}

pub fn inventory(work: &Path, prior: &Path, output: &Path) -> Result<Value, String> {
    let work = fs::canonicalize(work).map_err(|e| e.to_string())?;
    if output.starts_with(&work) {
        return Err("write the inventory outside its scan root".into());
    }
    let prior_doc: Value = read(prior)?;
    let mut exposure = Exposure {
        evidence: vec![input::identity(prior)?],
        inventory_scope: format!(
            "Conservative prior resolved-exposure records plus bounded filename/metadata inventory of {}; does not prove absence of renamed waveform copies or external/private exposure",
            work.display()
        ),
        ..Default::default()
    };
    for key in prior_doc["identified_exposure_keys"]
        .as_array()
        .into_iter()
        .flatten()
    {
        if let Some(id) = key
            .as_str()
            .and_then(|s| s.strip_prefix("public_satnogs:"))
            .and_then(|s| s.parse::<u64>().ok())
        {
            exposure.observation_ids.insert(id);
        }
    }
    for row in prior_doc["resolved_exposure_records"]
        .as_array()
        .into_iter()
        .flatten()
    {
        if let (Some(norad), Some(start), Some(end)) = (
            row["norad"].as_u64(),
            row["start"].as_i64(),
            row["end"].as_i64(),
        ) {
            // Extend to neighboring days to exclude overlapping/cross-midnight
            // passes in either the private or public archive namespace.
            for time in [
                start.saturating_sub(86_400),
                start,
                end,
                end.saturating_add(86_400),
            ] {
                if let Some(date) = chrono::DateTime::from_timestamp(time, 0) {
                    exposure
                        .excluded_mission_days
                        .insert(format!("{norad}:{}", date.format("%Y-%m-%d")));
                }
            }
        }
        if row["namespace"] == "public_satnogs" {
            if let Some(id) = row["id"].as_u64() {
                exposure.observation_ids.insert(id);
            }
            if let Some(station) = row["station"].as_u64() {
                exposure.stations.insert(station);
            }
        }
    }
    let mut stack = vec![work.clone()];
    let mut files = 0;
    let mut metadata_bytes = 0u64;
    let mut read_metadata = Vec::new();
    let mut issues = Vec::new();
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            if name.starts_with('.')
                || ["env", "venv", "node_modules", "target", "__pycache__"].contains(&name.as_str())
            {
                continue;
            }
            path_ids(&name, &mut exposure.observation_ids);
            if kind.is_dir() {
                stack.push(path);
                continue;
            }
            if !kind.is_file() {
                continue;
            }
            files += 1;
            if files > 1_000_000 {
                return Err("exposure inventory file bound reached".into());
            }
            if ![
                "cohort.json",
                "manifest.json",
                "metadata.json",
                "summary.json",
                "observations.json",
            ]
            .contains(&name.as_str())
            {
                continue;
            }
            let bytes = entry.metadata().map_err(|e| e.to_string())?.len();
            if bytes > 16 * 1024 * 1024 || metadata_bytes + bytes > 512 * 1024 * 1024 {
                issues.push(json!({"path":path,"reason":"metadata read bound"}));
                continue;
            }
            metadata_bytes += bytes;
            match read::<Value>(&path) {
                Ok(value) => {
                    value_ids(&value, &mut exposure.observation_ids);
                    read_metadata.push(input::identity(&path)?);
                }
                Err(error) => issues.push(json!({"path":path,"reason":error})),
            }
        }
    }
    read_metadata.sort_by(|a, b| a.path.cmp(&b.path));
    let audit = json!({"schema":"framelift-exposure-inventory-v1","root":work,"regular_files_seen":files,
        "metadata_bytes_read":metadata_bytes,"metadata_evidence":read_metadata,"issues":issues,
        "waveform_bytes_read":0,"complete_global_nonexposure_proof":false,
        "prior_evidence":exposure.evidence,"ids":exposure.observation_ids,"excluded_mission_days":exposure.excluded_mission_days});
    let audit_path = output.with_extension("inventory.json");
    input::write_json_new(&audit_path, &audit)?;
    exposure.evidence.push(input::identity(&audit_path)?);
    input::write_json_new(output, &exposure)?;
    Ok(
        json!({"output":output,"excluded_observation_ids":exposure.observation_ids.len(),"excluded_mission_days":exposure.excluded_mission_days.len(),"inventory_complete_attested":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn conservative_id_parser_is_bounded_and_does_not_conflate_private_ids() {
        let mut ids = BTreeSet::new();
        path_ids("obs-14123456-run2-5122.ogg", &mut ids);
        value_ids(
            &json!({"observations":[{"id":14123457},{"id":5122}],"frames":[{"count":14123458}]}),
            &mut ids,
        );
        assert_eq!(ids, BTreeSet::from([14123456, 14123457]));
    }
}
