//! Outcome-blind cohorts and public-random ranking, compatible with the Python
//! selection domain. Pulse certificate binding is NOT signature verification.

use base64::{Engine, engine::general_purpose::STANDARD};
use chrono::{DateTime, SecondsFormat, Utc};
use serde_json::{Value, json};
use sha2::{Digest, Sha256, Sha512};
use std::collections::{BTreeMap, BTreeSet};

fn text<'a>(value: &'a Value, name: &str) -> Result<&'a str, String> {
    value
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| format!("{name} must be non-empty text"))
}

fn positive(value: &Value, name: &str) -> Result<u64, String> {
    value
        .as_u64()
        .filter(|v| *v > 0)
        .ok_or_else(|| format!("{name} must be a positive integer"))
}

fn hex_bytes(value: &str, size: usize, name: &str) -> Result<Vec<u8>, String> {
    if value.len() != size * 2 {
        return Err(format!("{name} must contain exactly {size} bytes"));
    }
    hex::decode(value).map_err(|_| format!("{name} must be hexadecimal"))
}

pub fn derive_selection_salt(
    domain: &str,
    preregistration_sha256: &str,
    output_value: &str,
) -> Result<[u8; 32], String> {
    if domain.is_empty() || domain.contains('\n') {
        return Err("selection domain must be non-empty and single-line".into());
    }
    if preregistration_sha256
        .bytes()
        .any(|b| b.is_ascii_uppercase())
    {
        return Err("preregistration_sha256 must be lowercase SHA-256".into());
    }
    let pre = hex_bytes(preregistration_sha256, 32, "preregistration_sha256")?;
    let output = hex_bytes(output_value, 64, "output_value")?;
    let mut hash = Sha256::new();
    hash.update(domain.as_bytes());
    hash.update([0]);
    hash.update(pre);
    hash.update([0]);
    hash.update(output);
    Ok(hash.finalize().into())
}

pub fn rank_integer(salt: &[u8; 32], identifier: u64) -> Result<String, String> {
    if identifier == 0 {
        return Err("identifier must be a positive integer".into());
    }
    let encoded = identifier.to_be_bytes();
    let start = identifier.leading_zeros() as usize / 8;
    let bytes = &encoded[start..];
    let mut hash = Sha256::new();
    hash.update(b"telemetry-yield/rank-positive-integer/v1\0");
    hash.update(salt);
    hash.update((bytes.len() as u16).to_be_bytes());
    hash.update(bytes);
    Ok(hex::encode(hash.finalize()))
}

pub fn validate_nist_beacon_pulse(
    document: &Value,
    expected_timestamp: &str,
    certificate_pem: &str,
) -> Result<Value, String> {
    let pulse = document
        .get("pulse")
        .filter(|v| v.is_object())
        .ok_or("NIST response must contain one pulse object")?;
    let time = |s: &str| {
        DateTime::parse_from_rfc3339(s)
            .map(|v| v.with_timezone(&Utc))
            .map_err(|_| "beacon timeStamp must be RFC3339 with UTC offset".to_string())
    };
    let actual = time(text(&pulse["timeStamp"], "timeStamp")?)?;
    if actual != time(expected_timestamp)? {
        return Err("NIST pulse timestamp differs from preregistration".into());
    }
    if pulse["version"] != "2.0" || pulse["period"].as_u64() != Some(60000) {
        return Err("unexpected NIST beacon version or period".into());
    }
    if pulse["statusCode"].as_u64() != Some(0) {
        return Err("NIST beacon pulse is not healthy".into());
    }
    let output = text(&pulse["outputValue"], "outputValue")?;
    let output_bytes = hex_bytes(output, 64, "outputValue")?;
    let certificate_id = text(&pulse["certificateId"], "certificateId")?;
    hex_bytes(certificate_id, 64, "certificateId")?;
    let signature = text(&pulse["signatureValue"], "signatureValue")?;
    let signature_bytes = hex::decode(signature)
        .map_err(|_| "NIST signatureValue must be complete hexadecimal bytes")?;
    let pem = certificate_pem.trim();
    let encoded = pem
        .strip_prefix("-----BEGIN CERTIFICATE-----")
        .and_then(|v| v.strip_suffix("-----END CERTIFICATE-----"))
        .ok_or("invalid NIST certificate PEM")?;
    if encoded.len() > 1024 * 1024 {
        return Err("certificate PEM exceeds 1 MiB bound".into());
    }
    let encoded: String = encoded
        .chars()
        .filter(|c| !c.is_ascii_whitespace())
        .collect();
    let der = STANDARD
        .decode(encoded)
        .map_err(|_| "invalid NIST certificate PEM base64")?;
    if der.is_empty() {
        return Err("empty NIST certificate DER".into());
    }
    let certificate_hash = hex::encode(Sha512::digest(der));
    if !certificate_id.eq_ignore_ascii_case(&certificate_hash) {
        return Err("NIST certificate SHA-512 does not match certificateId".into());
    }
    let chain = positive(&pulse["chainIndex"], "chainIndex")?;
    let index = positive(&pulse["pulseIndex"], "pulseIndex")?;
    Ok(
        json!({"version":"2.0","time_stamp":actual.to_rfc3339_opts(SecondsFormat::Millis,true),
        "period_milliseconds":60000,"chain_index":chain,"pulse_index":index,"uri":pulse["uri"],
        "output_value":output.to_ascii_uppercase(),"output_value_sha256":hex::encode(Sha256::digest(output_bytes)),
        "certificate_id":certificate_id.to_ascii_lowercase(),"certificate_der_sha512":certificate_hash,
        "signature_value_sha256":hex::encode(Sha256::digest(signature_bytes)),
        "certificate_identifier_verified":true,"pulse_signature_verified":false}),
    )
}

pub fn build_eligible_pool(
    catalogue: &Value,
    download_plan: &Value,
    eligible_modes: &[String],
    development_exclusions: &[u64],
) -> Result<Vec<Value>, String> {
    let candidates = download_plan["candidates"]
        .as_array()
        .ok_or("inputs must contain candidate and observation arrays")?;
    let observations = catalogue["observations"]
        .as_array()
        .ok_or("inputs must contain candidate and observation arrays")?;
    for mode in eligible_modes {
        super::nonempty("eligible mode", mode)?;
    }
    if development_exclusions.contains(&0) {
        return Err("development exclusion must be positive".into());
    }
    let exclusions: BTreeSet<_> = development_exclusions.iter().copied().collect();
    let modes: BTreeSet<_> = eligible_modes.iter().map(String::as_str).collect();
    let mut objects = BTreeMap::new();
    for raw in candidates {
        let id = positive(&raw["observation_id"], "observation_id")?;
        let key = text(&raw["key"], "key")?;
        if key != format!("observation_{id}.iq") {
            return Err(format!("object key/ID mismatch: {key}"));
        }
        let size = raw["size_bytes"]
            .as_u64()
            .ok_or("size_bytes must be nonnegative integer")?;
        if size == 0 {
            continue;
        }
        if size % 4 != 0 {
            return Err("CI16 object size must be a multiple of four".into());
        }
        let object = json!({"object_key":key,"size_bytes":size,"url":text(&raw["url"],"url")?});
        if objects.insert(id, object).is_some() {
            return Err(format!("duplicate candidate ID {id}"));
        }
    }
    let mut projected = BTreeMap::new();
    for raw in observations {
        if !raw.is_object() {
            return Err("catalogue observation must be an object".into());
        }
        let Some(id) = raw["observation_id"].as_u64() else {
            continue;
        };
        if !objects.contains_key(&id) {
            continue;
        }
        let mut row = serde_json::Map::new();
        for field in [
            "observation_id",
            "satellite_id",
            "mode",
            "frequency_hz",
            "start",
            "end",
        ] {
            row.insert(field.into(), raw[field].clone());
        }
        if projected.insert(id, row).is_some() {
            return Err(format!("duplicate catalogue observation {id}"));
        }
    }
    let mut pool = Vec::new();
    for (id, mut row) in projected {
        if exclusions.contains(&id) {
            continue;
        }
        if !modes.contains(text(&row["mode"], "mode")?) {
            continue;
        }
        text(&row["satellite_id"], "satellite_id")?;
        let frequency = row["frequency_hz"]
            .as_f64()
            .ok_or("frequency_hz must be positive")?;
        if !frequency.is_finite() || frequency <= 0.0 {
            return Err("frequency_hz must be positive and finite".into());
        }
        row.extend(objects[&id].as_object().unwrap().clone());
        pool.push(Value::Object(row));
    }
    Ok(pool)
}

pub fn select_public_random_cohort(
    pool: &[Value],
    salt: &[u8; 32],
    target: usize,
    maximum_per_satellite: usize,
    minimum_satellites: usize,
) -> Result<Vec<Value>, String> {
    if target == 0 || maximum_per_satellite == 0 || minimum_satellites == 0 {
        return Err("selection counts must be positive".into());
    }
    let mut ranked = Vec::new();
    let mut seen = BTreeSet::new();
    for raw in pool {
        let id = positive(&raw["observation_id"], "observation_id")?;
        if !seen.insert(id) {
            return Err("eligible pool contains duplicate observation IDs".into());
        }
        let rank = rank_integer(salt, id)?;
        let mut row = raw.clone();
        row["selection_rank_sha256"] = json!(rank);
        ranked.push((rank, id, row));
    }
    ranked.sort_by(|a, b| (&a.0, a.1).cmp(&(&b.0, b.1)));
    let mut counts = BTreeMap::<String, usize>::new();
    let mut selected = Vec::new();
    for (_, _, row) in ranked {
        let satellite = text(&row["satellite_id"], "satellite_id")?.to_owned();
        let count = counts.entry(satellite).or_default();
        if *count >= maximum_per_satellite {
            continue;
        }
        *count += 1;
        selected.push(row);
        if selected.len() == target {
            break;
        }
    }
    if selected.len() != target {
        return Err("insufficient eligible observations after diversity cap".into());
    }
    if counts.len() < minimum_satellites {
        return Err("selected cohort has too few satellite identities".into());
    }
    Ok(selected)
}

#[cfg(test)]
#[path = "selection_tests.rs"]
mod tests;
