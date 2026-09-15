//! Independent, fail-closed comparison against the frozen Python OGG campaign.
//!
//! This module does not call the receiver's CRC or framing implementation. A
//! matching frame count is never accepted as byte equality. The supplied
//! reference summary is the trust anchor; its SHA-256 is included in the audit.
//! The caller must retain that hash separately if defending against replacement
//! of the *entire* reference dataset, rather than corruption of one artifact.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};

type AuditResult<T> = Result<T, String>;
const MAX_JSON_BYTES: u64 = 64 * 1024 * 1024;
const MAX_JOURNAL_BYTES: u64 = 64 * 1024 * 1024;

fn read_json(path: &Path) -> AuditResult<Value> {
    let meta = fs::metadata(path).map_err(|e| format!("{}: {e}", path.display()))?;
    if !meta.is_file() || meta.len() > MAX_JSON_BYTES {
        return Err(format!("not a bounded JSON file: {}", path.display()));
    }
    serde_json::from_reader(BufReader::new(
        File::open(path).map_err(|e| format!("{}: {e}", path.display()))?,
    ))
    .map_err(|e| format!("{}: {e}", path.display()))
}

fn digest_file(path: &Path) -> AuditResult<(String, u64)> {
    let mut file = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 65536];
    let mut bytes = 0_u64;
    loop {
        let n = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        digest.update(&buffer[..n]);
        bytes += n as u64;
    }
    Ok((hex::encode(digest.finalize()), bytes))
}

fn text<'a>(value: &'a Value, key: &str) -> AuditResult<&'a str> {
    value[key]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| format!("missing/invalid string field {key}"))
}

fn integer(value: &Value, key: &str) -> AuditResult<u64> {
    value[key]
        .as_u64()
        .ok_or_else(|| format!("missing/invalid nonnegative integer field {key}"))
}

fn number(value: &Value, key: &str) -> AuditResult<f64> {
    value[key]
        .as_f64()
        .filter(|n| n.is_finite())
        .ok_or_else(|| format!("missing/invalid finite number field {key}"))
}

fn array<'a>(value: &'a Value, key: &str) -> AuditResult<&'a Vec<Value>> {
    value[key]
        .as_array()
        .ok_or_else(|| format!("missing/invalid array field {key}"))
}

fn require(condition: bool, message: impl Into<String>) -> AuditResult<()> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}

fn sha_string(value: &Value, key: &str) -> AuditResult<String> {
    let encoded = text(value, key)?;
    require(
        encoded.len() == 64
            && encoded
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        format!("invalid SHA-256 field {key}"),
    )?;
    Ok(encoded.to_string())
}

fn inside(root: &Path, path: &Path) -> AuditResult<PathBuf> {
    let canonical = path
        .canonicalize()
        .map_err(|e| format!("{}: {e}", path.display()))?;
    require(
        canonical.starts_with(root),
        format!("artifact escapes its root: {}", path.display()),
    )?;
    Ok(canonical)
}

fn verify_identity(root: &Path, identity: &Value) -> AuditResult<PathBuf> {
    let path = inside(root, Path::new(text(identity, "path")?))?;
    require(
        fs::metadata(&path).map_err(|e| e.to_string())?.is_file(),
        "artifact is not a regular file",
    )?;
    let wanted_hash = sha_string(identity, "sha256")?;
    let wanted_bytes = integer(identity, "bytes")?;
    let (hash, bytes) = digest_file(&path)?;
    require(
        hash == wanted_hash && bytes == wanted_bytes,
        format!("artifact identity mismatch: {}", path.display()),
    )?;
    Ok(path)
}

fn read_journal(path: &Path) -> AuditResult<Vec<Value>> {
    let meta = fs::metadata(path).map_err(|e| format!("{}: {e}", path.display()))?;
    require(
        meta.is_file() && meta.len() <= MAX_JOURNAL_BYTES,
        "journal is not a bounded regular file",
    )?;
    let file = File::open(path).map_err(|e| e.to_string())?;
    let mut rows = Vec::new();
    for (index, line) in BufReader::new(file).lines().enumerate() {
        let line = line.map_err(|e| e.to_string())?;
        require(!line.trim().is_empty(), "blank/truncated journal row")?;
        rows.push(
            serde_json::from_str(&line)
                .map_err(|e| format!("{} row {}: {e}", path.display(), index + 1))?,
        );
    }
    require(
        !rows.is_empty(),
        "empty window journal cannot prove completion",
    )?;
    Ok(rows)
}

// Deliberately independent of crate::protocol, including address validation.
pub(crate) fn valid_ax25_ui_fcs(frame: &[u8]) -> bool {
    if frame.len() < 18 {
        return false;
    }
    let payload = &frame[..frame.len() - 2];
    let mut crc = 0xffff_u16;
    for byte in payload {
        crc ^= u16::from(*byte);
        for _ in 0..8 {
            crc = (crc >> 1) ^ if crc & 1 == 1 { 0x8408 } else { 0 };
        }
    }
    if (!crc).to_le_bytes() != frame[frame.len() - 2..] {
        return false;
    }
    for index in 0..10 {
        let offset = index * 7;
        if payload.len() < offset + 7 {
            return false;
        }
        let address = &payload[offset..offset + 7];
        let mut saw_letter = false;
        let mut saw_space = false;
        for encoded in &address[..6] {
            if encoded & 1 != 0 {
                return false;
            }
            let ch = encoded >> 1;
            if ch == b' ' {
                saw_space = true;
            } else if ch.is_ascii_uppercase() || ch.is_ascii_digit() {
                if saw_space {
                    return false;
                }
                saw_letter = true;
            } else {
                return false;
            }
        }
        if !saw_letter || address[6] & 0x60 != 0x60 {
            return false;
        }
        if address[6] & 1 != 0 {
            return index >= 1 && payload.len() >= offset + 9 && payload[offset + 7] == 0x03;
        }
    }
    false
}

#[derive(Clone)]
struct Frame {
    pdu: Vec<u8>,
    full: Vec<u8>,
    sha256: String,
    provenance: Vec<Value>,
    validation: String,
}

fn decode_hex(value: &Value, key: &str) -> AuditResult<Vec<u8>> {
    hex::decode(text(value, key)?).map_err(|e| format!("invalid {key}: {e}"))
}

fn checked_frames(result: &Value, windows: &[Value]) -> AuditResult<BTreeMap<Vec<u8>, Frame>> {
    require(
        integer(result, "window_count")? == windows.len() as u64,
        "window count differs from journal",
    )?;
    require(
        integer(result, "failed_window_count")? == 0,
        "failed windows are not completed zero-frame windows",
    )?;
    let mut offsets = BTreeMap::new();
    let mut previous = None;
    for (index, window) in windows.iter().enumerate() {
        let offset = number(window, "offset_seconds")?;
        require(offset >= 0.0, "negative window offset")?;
        if let Some(previous) = previous {
            require(offset > previous, "window journal is not strictly ordered")?;
        }
        previous = Some(offset);
        require(
            array(window, "failures")?.is_empty(),
            "window failure cannot count as a negative result",
        )?;
        require(integer(window, "samples")? > 0, "empty decoded window")?;
        require(
            integer(window, "timing_attempts")? > 0,
            "no timing hypotheses were executed",
        )?;
        integer(window, "frame_instances")?;
        integer(window, "unique_total")?;
        offsets.insert(offset.to_bits(), index);
    }
    let mut frames = BTreeMap::new();
    let mut instances = vec![0_u64; windows.len()];
    let mut by_window = vec![BTreeSet::new(); windows.len()];
    for raw in array(result, "frames")? {
        let pdu = decode_hex(raw, "payload_hex")?;
        let full = decode_hex(raw, "frame_with_fcs_hex")?;
        require(
            full.len() == pdu.len() + 2 && full.starts_with(&pdu),
            "PDU is not the payload of the stored FCS frame",
        )?;
        require(
            valid_ax25_ui_fcs(&full),
            "independent AX.25/FCS validation failed",
        )?;
        require(
            raw["independent_bitwise_crc_passed"] == true,
            "result lacks explicit independent CRC success",
        )?;
        let sha256 = sha_string(raw, "payload_sha256")?;
        require(
            sha256 == hex::encode(Sha256::digest(&pdu)),
            "payload SHA-256 does not match payload bytes",
        )?;
        require(
            integer(raw, "payload_bytes")? == pdu.len() as u64,
            "payload length does not match payload bytes",
        )?;
        let validation = text(raw, "validation")?.to_string();
        require(
            validation == "crc16_x25+ax25_ui",
            "unexpected validation policy for frozen AX.25 campaign",
        )?;
        let provenance = array(raw, "provenance")?.clone();
        require(!provenance.is_empty(), "frame has no recovery provenance")?;
        let mut previous_index = None;
        for source in &provenance {
            let offset = number(source, "window_start_seconds")?;
            let index = *offsets
                .get(&offset.to_bits())
                .ok_or("frame provenance refers to an unexecuted window")?;
            if let Some(previous_index) = previous_index {
                require(
                    index > previous_index,
                    "unordered or duplicate frame provenance",
                )?;
            }
            previous_index = Some(index);
            let rank = integer(source, "timing_rank")?;
            require(
                rank < integer(&windows[index], "timing_attempts")?,
                "provenance timing rank exceeds executed hypothesis count",
            )?;
            number(source, "timing_score")?;
            text(source, "waveform")?;
            instances[index] += 1;
            by_window[index].insert(pdu.clone());
        }
        let frame = Frame {
            pdu: pdu.clone(),
            full,
            sha256,
            provenance,
            validation,
        };
        require(
            frames.insert(pdu, frame).is_none(),
            "duplicate PDU records in result",
        )?;
    }
    let mut cumulative = BTreeSet::new();
    for (index, row) in windows.iter().enumerate() {
        cumulative.extend(by_window[index].iter().cloned());
        require(
            integer(row, "frame_instances")? == instances[index],
            "journal frame instances disagree with frame provenance",
        )?;
        require(
            integer(row, "unique_total")? == cumulative.len() as u64,
            "journal cumulative PDU count disagrees with frame provenance",
        )?;
    }
    require(
        integer(result, "unique_pdu_count")? == frames.len() as u64,
        "reported unique count differs from exact PDU set",
    )?;
    require(
        integer(result, "unique_pdu_bytes")?
            == frames.values().map(|f| f.pdu.len() as u64).sum::<u64>(),
        "reported unique bytes differ from exact PDU set",
    )?;
    Ok(frames)
}

struct Campaign {
    root: PathBuf,
    summary: Value,
    sha256: String,
}

fn load_campaign(path: &Path) -> AuditResult<Campaign> {
    let summary_path = path.canonicalize().map_err(|e| e.to_string())?;
    let summary = read_json(&summary_path)?;
    require(
        text(&summary, "schema")? == "ogg-archive-campaign-summary-v1",
        "not a frozen terminal campaign summary",
    )?;
    require(
        summary["complete_campaign"] == true,
        "reference campaign is incomplete",
    )?;
    let rows = array(&summary, "observations")?;
    require(!rows.is_empty(), "empty cohort cannot prove equivalence")?;
    require(
        integer(&summary, "frozen_count")? == rows.len() as u64,
        "frozen observation count differs from supplied cohort",
    )?;
    let complete = rows
        .iter()
        .filter(|row| row["status"] == "complete")
        .count();
    require(complete > 0, "no completed reference comparisons")?;
    require(
        integer(&summary, "complete_comparisons")? == complete as u64,
        "reference completion count disagrees with observation rows",
    )?;
    let mut identifiers = BTreeSet::new();
    for row in rows {
        require(
            identifiers.insert(integer(row, "observation_id")?),
            "duplicate observation ID in reference",
        )?;
        text(row, "status")?;
    }
    let root = summary_path
        .parent()
        .and_then(Path::parent)
        .ok_or("reference must be under CAMPAIGN/summaries/")?
        .to_path_buf();
    require(
        summary_path
            .parent()
            .and_then(Path::file_name)
            .and_then(|s| s.to_str())
            == Some("summaries"),
        "reference must be under CAMPAIGN/summaries/",
    )?;
    let plan_hash = digest_file(&root.join("plan.json"))?.0;
    require(
        plan_hash == sha_string(&summary, "plan_sha256")?,
        "frozen campaign plan hash mismatch",
    )?;
    let sha256 = digest_file(&summary_path)?.0;
    Ok(Campaign {
        root,
        summary,
        sha256,
    })
}

struct Reference {
    id: u64,
    native: Value,
    windows: Vec<Value>,
    frames: BTreeMap<Vec<u8>, Frame>,
    artifact_count: usize,
}

fn verify_reference(campaign: &Campaign, row: &Value) -> AuditResult<Reference> {
    let id = integer(row, "observation_id")?;
    require(
        row["status"] == "complete",
        "reference observation is not complete",
    )?;
    require(
        row["native_status"] == "complete" && row["baseline_status"] == "complete",
        "reference receiver subprocess did not complete",
    )?;
    require(
        row["plan_sha256"] == campaign.summary["plan_sha256"],
        "observation belongs to a different frozen campaign plan",
    )?;
    let attempt = inside(&campaign.root, Path::new(text(row, "attempt")?))?;
    let commit = read_json(&attempt.join("commit.json"))?;
    let final_path = verify_identity(&campaign.root, &commit["final"])?;
    require(
        final_path == attempt.join("final.json"),
        "commit points at a different observation final",
    )?;
    require(
        read_json(&final_path)? == *row,
        "reference summary differs from committed final",
    )?;
    let mut artifacts = BTreeMap::new();
    for artifact in array(row, "artifacts")? {
        let path = verify_identity(&campaign.root, artifact)?;
        require(
            artifacts.insert(path, artifact).is_none(),
            "duplicate frozen artifact identity",
        )?;
    }
    for name in [
        "native.json",
        "native.plan.json",
        "native.windows.jsonl",
        "input-manifest.json",
    ] {
        require(
            artifacts.contains_key(&attempt.join(name)),
            format!("required reference artifact is not committed: {name}"),
        )?;
    }
    let native = read_json(&attempt.join("native.json"))?;
    let input = read_json(&attempt.join("input-manifest.json"))?;
    let plan = read_json(&attempt.join("native.plan.json"))?;
    require(
        text(&native, "schema")? == "native-audio-refinement-result-v2",
        "unexpected native reference schema",
    )?;
    require(
        verify_identity(&campaign.root, &native["plan_identity"])?
            == attempt.join("native.plan.json"),
        "native result points to a different search plan",
    )?;
    let input_sha = sha_string(&native["input"], "sha256")?;
    require(
        input_sha == sha_string(&input["wav"], "sha256")?
            && input_sha == sha_string(&row["comparison"], "same_audio_sha256")?
            && input_sha == sha_string(&plan["input"], "sha256")?,
        "reference input identity disagreement",
    )?;
    require(
        integer(&input, "observation_id")? == id,
        "input manifest belongs to a different observation",
    )?;
    require(
        integer(&native["input"], "bytes")? == integer(&input["wav"], "bytes")?,
        "reference WAV byte count disagreement",
    )?;
    let windows = read_journal(&attempt.join("native.windows.jsonl"))?;
    let frames = checked_frames(&native, &windows)?;
    let declared = &row["comparison"]["counts"]["native_same_audio"];
    let declared_pdus: BTreeSet<Vec<u8>> = array(declared, "pdus")?
        .iter()
        .map(|p| {
            hex::decode(p.as_str().ok_or("non-string comparison PDU")?).map_err(|e| e.to_string())
        })
        .collect::<AuditResult<_>>()?;
    require(
        declared_pdus.len() == array(declared, "pdus")?.len()
            && declared_pdus == frames.keys().cloned().collect(),
        "committed comparison PDU set disagrees with native artifact",
    )?;
    require(
        integer(declared, "count")? == frames.len() as u64
            && integer(declared, "bytes")? == integer(&native, "unique_pdu_bytes")?,
        "committed comparison count/bytes disagree with native artifact",
    )?;
    Ok(Reference {
        id,
        native,
        windows,
        frames,
        artifact_count: artifacts.len(),
    })
}

fn without_score(provenance: &Value) -> Value {
    let mut projection = provenance.clone();
    if let Some(object) = projection.as_object_mut() {
        object.remove("timing_score");
    }
    projection
}

fn compare_one(reference: &Reference, output_dir: &Path) -> AuditResult<Value> {
    let root = output_dir
        .canonicalize()
        .map_err(|e| format!("missing result directory: {e}"))?;
    let result_path = inside(&root, &root.join("result.json"))?;
    let journal_path = inside(&root, &root.join("windows.jsonl"))?;
    let result = read_json(&result_path)?;
    require(
        result["status"] == "complete",
        format!("observation {} did not complete successfully", reference.id),
    )?;
    require(
        integer(&result, "observation_id")? == reference.id,
        "result observation ID differs from directory/reference",
    )?;
    require(
        sha_string(&result["input"], "sha256")?
            == sha_string(&reference.native["input"], "sha256")?
            && integer(&result["input"], "bytes")? == integer(&reference.native["input"], "bytes")?,
        "not the exact same WAV input; input SHA-256/size mismatch",
    )?;
    let windows = read_journal(&journal_path)?;
    let frames = checked_frames(&result, &windows)?;
    let mut missing = Vec::new();
    let mut additional = Vec::new();
    let mut fcs_differences = Vec::new();
    let mut provenance_differences = Vec::new();
    let mut score_comparisons = 0_usize;
    let mut score_differences = 0_usize;
    let mut score_max_abs_difference = 0.0_f64;
    for (pdu, expected) in &reference.frames {
        let Some(actual) = frames.get(pdu) else {
            missing.push(expected.sha256.clone());
            continue;
        };
        if actual.full != expected.full || actual.validation != expected.validation {
            fcs_differences.push(expected.sha256.clone());
        }
        let expected_discrete: Vec<_> = expected.provenance.iter().map(without_score).collect();
        let actual_discrete: Vec<_> = actual.provenance.iter().map(without_score).collect();
        if expected_discrete != actual_discrete {
            provenance_differences.push(expected.sha256.clone());
        } else {
            for (a, b) in expected.provenance.iter().zip(&actual.provenance) {
                let a = number(a, "timing_score")?;
                let b = number(b, "timing_score")?;
                score_comparisons += 1;
                if a.to_bits() != b.to_bits() {
                    score_differences += 1;
                    score_max_abs_difference = score_max_abs_difference.max((a - b).abs());
                }
            }
        }
    }
    for (pdu, frame) in &frames {
        if !reference.frames.contains_key(pdu) {
            additional.push(frame.sha256.clone());
        }
    }
    let differing_window_indices: Vec<_> = (0..windows.len().max(reference.windows.len()))
        .filter(|i| windows.get(*i) != reference.windows.get(*i))
        .collect();
    let exact_frame_set = missing.is_empty() && additional.is_empty() && fcs_differences.is_empty();
    let pass =
        exact_frame_set && provenance_differences.is_empty() && differing_window_indices.is_empty();
    let expected_instances: usize = reference.frames.values().map(|f| f.provenance.len()).sum();
    let elapsed_reference = number(&reference.native, "elapsed_seconds")?;
    let elapsed_candidate = number(&result, "elapsed_seconds")?;
    require(
        elapsed_reference >= 0.0 && elapsed_candidate >= 0.0,
        "negative elapsed time",
    )?;
    Ok(json!({
        "observation_id": reference.id,
        "pass": pass,
        "exact_pdu_and_received_fcs_sets_equal": exact_frame_set,
        "ordered_discrete_provenance_equal": provenance_differences.is_empty() && exact_frame_set,
        "ordered_window_journal_equal": differing_window_indices.is_empty(),
        "same_wav_file_sha256": reference.native["input"]["sha256"],
        "reference_unique_pdus": reference.frames.len(),
        "candidate_unique_pdus": frames.len(),
        "reference_unique_pdu_bytes": reference.native["unique_pdu_bytes"],
        "candidate_unique_pdu_bytes": result["unique_pdu_bytes"],
        "reference_window_count": reference.windows.len(),
        "candidate_window_count": windows.len(),
        "missing_pdu_sha256": missing,
        "additional_pdu_sha256": additional,
        "received_fcs_or_validation_difference_pdu_sha256": fcs_differences,
        "ordered_provenance_difference_pdu_sha256": provenance_differences,
        "differing_window_indices_zero_based": differing_window_indices,
        "timing_scores": {
            "expected_provenance_records": expected_instances,
            "compared": score_comparisons,
            "bitwise_differences": score_differences,
            "maximum_absolute_difference": score_max_abs_difference,
            "all_reference_records_compared": score_comparisons == expected_instances,
            "all_bitwise_equal": score_comparisons == expected_instances && score_differences == 0,
            "not_used_as_a_tolerance_for_discrete_decisions": true
        },
        "reference_artifacts_hash_verified": reference.artifact_count,
        "result_sha256": digest_file(&result_path)?.0,
        "windows_jsonl_sha256": digest_file(&journal_path)?.0,
        "recorded_elapsed_seconds": {"reference": elapsed_reference, "candidate": elapsed_candidate},
        "elapsed_is_not_an_isolated_or_boundary_matched_speed_benchmark": true
    }))
}

/// Audit one explicit observation as a canary. A pass is NOT a campaign pass.
pub fn compare_observation(
    reference_summary: &Path,
    observation_id: u64,
    result_dir: &Path,
) -> AuditResult<Value> {
    let campaign = load_campaign(reference_summary)?;
    let row = array(&campaign.summary, "observations")?
        .iter()
        .find(|r| r["observation_id"].as_u64() == Some(observation_id))
        .ok_or("requested observation is not in the frozen cohort")?;
    let reference = verify_reference(&campaign, row)?;
    let comparison = compare_one(&reference, result_dir)?;
    Ok(json!({
        "schema": "rust-receiver-observation-parity-audit-v1",
        "scope": "one_explicit_observation_not_campaign_qualification",
        "reference_summary_sha256": campaign.sha256,
        "pass": comparison["pass"],
        "comparison": comparison,
        "universal_zero_accuracy_loss_proven": false,
        "reference_noise_controls_replayed": false
    }))
}

/// Compare every successful frozen observation. Missing/failed/extra outputs
/// fail closed; bytes, received FCS, provenance and windows are compared exactly.
pub fn compare_campaign(reference_summary: &Path, results_root: &Path) -> AuditResult<Value> {
    let campaign = load_campaign(reference_summary)?;
    let rows: Vec<_> = array(&campaign.summary, "observations")?
        .iter()
        .filter(|row| row["status"] == "complete")
        .collect();
    let ids: BTreeSet<_> = rows
        .iter()
        .map(|row| integer(row, "observation_id"))
        .collect::<AuditResult<_>>()?;
    let root = results_root.canonicalize().map_err(|e| e.to_string())?;
    for entry in fs::read_dir(&root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        let numeric_id = name.parse::<u64>().ok();
        if numeric_id.is_some()
            || path.join("result.json").exists()
            || path.join("windows.jsonl").exists()
        {
            require(
                numeric_id.is_some_and(|id| ids.contains(&id) && name == id.to_string())
                    && path.is_dir(),
                format!("extra or malformed observation output: {name}"),
            )?;
            inside(&root, &path)?;
        }
    }
    let mut comparisons = Vec::new();
    let mut reference_global = BTreeSet::new();
    let mut native_total = 0_u64;
    for row in rows {
        let reference = verify_reference(&campaign, row)
            .map_err(|e| format!("reference observation {}: {e}", row["observation_id"]))?;
        native_total += reference.frames.len() as u64;
        reference_global.extend(reference.frames.keys().cloned());
        let result_dir = inside(&root, &root.join(reference.id.to_string()))?;
        comparisons.push(
            compare_one(&reference, &result_dir)
                .map_err(|e| format!("candidate observation {}: {e}", reference.id))?,
        );
    }
    require(
        integer(
            &campaign.summary["per_observation_deduplicated_totals"]["native_same_audio"],
            "count",
        )? == native_total,
        "campaign native total disagrees with independently reconstructed PDU sets",
    )?;
    require(
        integer(
            &campaign.summary["global_union_of_observation_local_sets"]["native_same_audio"],
            "count",
        )? == reference_global.len() as u64,
        "campaign globally unique native count disagrees with PDU union",
    )?;
    let all_pass = comparisons.iter().all(|row| row["pass"] == true);
    let all_scores_equal = comparisons
        .iter()
        .all(|row| row["timing_scores"]["all_bitwise_equal"] == true);
    let sum = |field: &str| -> usize {
        comparisons
            .iter()
            .map(|row| row[field].as_array().map_or(0, Vec::len))
            .sum()
    };
    Ok(json!({
        "schema": "rust-receiver-campaign-parity-audit-v1",
        "scope": "all_complete_observations_in_frozen_reference_campaign",
        "pass": all_pass,
        "reference_summary_sha256": campaign.sha256,
        "reference_frozen_observations": campaign.summary["frozen_count"],
        "required_completed_observations": ids.len(),
        "compared_completed_observations": comparisons.len(),
        "reference_pdus_deduplicated_per_observation": native_total,
        "reference_pdus_deduplicated_globally": reference_global.len(),
        "missing_pdus_deduplicated_per_observation": sum("missing_pdu_sha256"),
        "additional_pdus_deduplicated_per_observation": sum("additional_pdu_sha256"),
        "received_fcs_or_validation_differences": sum("received_fcs_or_validation_difference_pdu_sha256"),
        "ordered_provenance_differences": sum("ordered_provenance_difference_pdu_sha256"),
        "differing_window_count": sum("differing_window_indices_zero_based"),
        "all_timing_scores_bitwise_equal": all_scores_equal,
        "reference_noise_controls_replayed": false,
        "universal_zero_accuracy_loss_proven": false,
        "publication_ready": false,
        "deployment_ready": false,
        "timing_caveat": "Frozen Python elapsed includes two smoke controls. Recorded elapsed values alone are not a boundary-matched or isolated speed benchmark.",
        "comparisons": comparisons
    }))
}

#[cfg(test)]
#[path = "tests/audit_campaign.rs"]
mod tests;

#[path = "audit_candidates.rs"]
pub mod candidates;
