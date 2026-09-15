//! Event-local, protocol-neutral union. This groups caller-validated evidence;
//! it does not independently validate CRC, source attribution, or repaired bits.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

/// Compatibility encoding with the original canonical JSON, including Python's
/// scientific exponent spelling. It is used only for identity, never DSP.
pub fn canonical_json(value: &Value) -> Result<String, String> {
    Ok(match value {
        Value::Null => "null".into(),
        Value::Bool(b) => b.to_string(),
        Value::String(s) => serde_json::to_string(s).map_err(|e| e.to_string())?,
        Value::Number(n) => {
            if !n.is_f64() {
                n.to_string()
            } else {
                let f = n.as_f64().ok_or("invalid floating JSON number")?;
                if !f.is_finite() {
                    return Err("canonical numbers must be finite".into());
                }
                let raw = format!("{f:?}");
                if let Some((mantissa, exponent)) = raw.split_once('e') {
                    let exponent: i32 = exponent.parse::<i32>().map_err(|e| e.to_string())?;
                    format!(
                        "{mantissa}e{}{abs:02}",
                        if exponent < 0 { "-" } else { "+" },
                        abs = exponent.abs()
                    )
                } else {
                    raw
                }
            }
        }
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(canonical_json)
                .collect::<Result<Vec<_>, _>>()?
                .join(",")
        ),
        Value::Object(map) => {
            let mut entries: Vec<_> = map.iter().collect();
            entries.sort_by(|a, b| a.0.cmp(b.0));
            let parts = entries
                .iter()
                .map(|(k, v)| {
                    Ok(format!(
                        "{}:{}",
                        serde_json::to_string(k).map_err(|e| e.to_string())?,
                        canonical_json(v)?
                    ))
                })
                .collect::<Result<Vec<String>, String>>()?;
            format!("{{{}}}", parts.join(","))
        }
    })
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DetectionRecord {
    pub capture_sha256: String,
    pub segment_start_sample: u64,
    pub segment_sample_count: u64,
    pub branch_id: String,
    pub source_role: String,
    pub plugin_id: String,
    pub plugin_version: String,
    pub protocol_id: String,
    pub original_frame_hex: String,
    pub normalized_payload_hex: String,
    pub validation_layers: Vec<String>,
    pub validated: bool,
    pub hypothesis_fingerprint: String,
    pub config_fingerprint: String,
    pub event_key: String,
    pub event_time_seconds: f64,
    #[serde(default = "empty_provenance")]
    pub provenance: Value,
}
fn empty_provenance() -> Value {
    json!({})
}
type LedgerKey = (String, String, String, String);
impl DetectionRecord {
    fn document(&self) -> Result<(LedgerKey, Value), String> {
        let capture = self.capture_sha256.to_ascii_lowercase();
        if capture.len() != 64 || !capture.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err("invalid capture SHA256".into());
        }
        if self.segment_sample_count == 0
            || !self.event_time_seconds.is_finite()
            || self.event_time_seconds < 0.0
        {
            return Err(
                "positive segment length and finite nonnegative event time required".into(),
            );
        }
        for name in [
            &self.branch_id,
            &self.plugin_id,
            &self.plugin_version,
            &self.protocol_id,
            &self.hypothesis_fingerprint,
            &self.config_fingerprint,
            &self.event_key,
        ] {
            if name.trim().is_empty() {
                return Err("empty detection identifier".into());
            }
        }
        if !matches!(self.source_role.as_str(), "baseline" | "candidate") {
            return Err("source role must be baseline or candidate".into());
        }
        let layers: BTreeSet<_> = self.validation_layers.iter().collect();
        if !self.validated
            || layers.is_empty()
            || layers.len() != self.validation_layers.len()
            || layers.iter().any(|s| s.trim().is_empty())
        {
            return Err("ledger accepts only explicitly validated detections with unique named validation layers".into());
        }
        if !self.provenance.is_object() {
            return Err("provenance must be an object".into());
        }
        let original = hex::decode(&self.original_frame_hex).map_err(|e| e.to_string())?;
        let payload = hex::decode(&self.normalized_payload_hex).map_err(|e| e.to_string())?;
        if original.is_empty()
            || payload.is_empty()
            || original.len() > 16 * 1024 * 1024
            || payload.len() > 16 * 1024 * 1024
        {
            return Err("frame/payload must be nonempty and at most16MiB".into());
        }
        let payload_sha = digest(&payload);
        let key = (
            capture.clone(),
            self.event_key.clone(),
            self.protocol_id.clone(),
            payload_sha.clone(),
        );
        let mut document = serde_json::to_value(self).map_err(|e| e.to_string())?;
        document["capture_sha256"] = json!(capture);
        document["original_frame_hex"] = json!(hex::encode(&original));
        document["normalized_payload_hex"] = json!(hex::encode(&payload));
        document["original_frame_sha256"] = json!(digest(&original));
        document["normalized_payload_sha256"] = json!(payload_sha);
        let origin = digest(canonical_json(&document)?.as_bytes());
        document["origin_sha256"] = json!(origin);
        Ok((key, document))
    }
}

pub fn build_candidate_ledger(records: &[DetectionRecord]) -> Result<Value, String> {
    if records.len() > 100_000 {
        return Err("ledger input exceeds100000 origins; use explicit partitions".into());
    }
    let mut origins: BTreeMap<String, (LedgerKey, Value)> = BTreeMap::new();
    for record in records {
        let (key, document) = record.document()?;
        let hash = document["origin_sha256"].as_str().unwrap().to_owned();
        if let Some((old_key, old)) = origins.get(&hash) {
            if old_key != &key || canonical_json(old)? != canonical_json(&document)? {
                return Err("origin fingerprint collision".into());
            }
        } else {
            origins.insert(hash, (key, document));
        }
    }
    let origin_count = origins.len();
    let mut groups: BTreeMap<LedgerKey, Vec<Value>> = BTreeMap::new();
    for (_, (key, document)) in origins {
        groups.entry(key).or_default().push(document);
    }
    let mut frames = Vec::new();
    let mut branch_counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut baseline_count = 0;
    let mut candidate_count = 0;
    for ((capture, event, protocol, payload_sha), origins) in groups {
        let payload = origins[0]["normalized_payload_hex"].clone();
        if origins
            .iter()
            .any(|o| o["normalized_payload_hex"] != payload)
        {
            return Err("normalized payload fingerprint collision".into());
        }
        let mut branches = BTreeSet::new();
        let mut original_hashes = BTreeSet::new();
        let baseline = origins.iter().any(|o| o["source_role"] == "baseline");
        let candidate = origins.iter().any(|o| o["source_role"] == "candidate");
        baseline_count += usize::from(baseline);
        candidate_count += usize::from(candidate);
        for o in &origins {
            branches.insert(o["branch_id"].as_str().unwrap().to_owned());
            original_hashes.insert(o["original_frame_sha256"].as_str().unwrap().to_owned());
        }
        for b in &branches {
            *branch_counts.entry(b.clone()).or_default() += 1;
        }
        frames.push(json!({"key":{"capture_sha256":capture,"event_key":event,"protocol_id":protocol,"normalized_payload_sha256":payload_sha},
            "normalized_payload_hex":payload,"original_frame_sha256s":original_hashes,"branch_ids":branches,
            "has_baseline_origin":baseline,"has_candidate_origin":candidate,"origins":origins}));
    }
    Ok(
        json!({"schema_version":1,"baseline_count":baseline_count,"candidate_count":candidate_count,"branch_counts":branch_counts,
        "union_count":frames.len(),"incremental_over_baseline":frames.len()-baseline_count,"origin_count":origin_count,"frames":frames}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn legacy_canonical_numeric_spelling() {
        for (value, expected) in [
            (1e-6, "1e-06"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (-0.0, "-0.0"),
            (0.0001, "0.0001"),
        ] {
            assert_eq!(canonical_json(&json!(value)).unwrap(), expected);
        }
        assert_eq!(
            canonical_json(&json!({"ą":1,"a":"\n"})).unwrap(),
            "{\"a\":\"\\n\\u0001\",\"ą\":1}"
        );
    }
    #[test]
    fn immutable_legacy_ledger_oracle() {
        let fixture: Value =
            serde_json::from_str(include_str!("tests/ledger_oracle.json")).unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let records: Vec<DetectionRecord> =
                serde_json::from_value(case["input"].clone()).unwrap();
            let actual = build_candidate_ledger(&records).unwrap();
            assert_eq!(actual, case["expected"], "{}", case["name"]);
        }
    }
    #[test]
    fn explicit_validation_is_not_inferred_from_role() {
        let fixture: Value =
            serde_json::from_str(include_str!("tests/ledger_oracle.json")).unwrap();
        let mut r: DetectionRecord =
            serde_json::from_value(fixture["cases"][1]["input"][0].clone()).unwrap();
        r.validated = false;
        r.source_role = "baseline".into();
        assert!(build_candidate_ledger(&[r]).is_err());
    }
}
