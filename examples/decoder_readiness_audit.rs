//! Fail-closed, read-only AX.25 UI receiver control-suite gate.
//! See docs/decoder-readiness-audit.md for the manifest and trust boundary.
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use telemetry_yield_rs::input;

const SCHEMA: &str = "decoder-control-suite-v1";
const MAX_JSON: u64 = 64 * 1024 * 1024;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct Artifact {
    path: String,
    sha256: String,
    bytes: u64,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Format {
    NativeAudio,
    Innovation,
    Progressive,
    /// IQ generic receiver, only AX.25/AX.25-encapsulated CCSDS framing.
    GenericIq,
    /// Frozen24-branch M&M clock/AX.25 stage on19200Hz/9600baud pre-clock WAV.
    /// The upstream IQ/FM frontend is outside this component's qualification.
    MmClock,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RequiredReceiver {
    label: String,
    format: Format,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Kind {
    Positive,
    Negative,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum ExpectationBasis {
    SyntheticExactInput,
    IndependentExactInput,
    KnownNegativeInput,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Run {
    receiver: String,
    artifact: Artifact,
    session_manifest: Option<Artifact>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Case {
    id: String,
    kind: Kind,
    source: Artifact,
    expected_pdu_hex: Vec<String>,
    expectation_basis: ExpectationBasis,
    expectation_note: String,
    outputs: Vec<Run>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Suite {
    schema: String,
    suite_id: String,
    required_receivers: Vec<RequiredReceiver>,
    cases: Vec<Case>,
}

// Field order deliberately matches progressive.rs::Manifest and
// progressive_audio.rs::PreparedMetadata: its session digest hashes typed JSON,
// not the original whitespace or a generic, key-sorted Value representation.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PreparedMetadata {
    source: Artifact,
    wav: Artifact,
    sample_rate: u32,
    samples: usize,
    conversion_command: Option<Vec<String>>,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ProgressiveManifest {
    schema: String,
    #[serde(default,skip_serializing_if="Option::is_none")]
    compute_identity: Option<Value>,
    audio: PreparedMetadata,
    executable_sha256: String,
    policy: Value,
}

#[derive(Clone, Debug, Serialize)]
struct Issue {
    code: String,
    detail: String,
}
fn issue(code: &str, detail: impl ToString) -> Issue {
    Issue {
        code: code.into(),
        detail: detail.to_string(),
    }
}
type Checked<T> = Result<T, Issue>;

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
fn validate_identity(value: &Artifact) -> Checked<()> {
    if !Path::new(&value.path).is_absolute() || !valid_hash(&value.sha256) || value.bytes == 0 {
        return Err(issue(
            "invalid_identity",
            "absolute path, lowercase SHA256 and nonzero byte size required",
        ));
    }
    Ok(())
}
fn verify_source(expected: &Artifact) -> Checked<()> {
    validate_identity(expected)?;
    let actual =
        input::identity(Path::new(&expected.path)).map_err(|e| issue("source_unavailable", e))?;
    if actual.sha256 != expected.sha256 || actual.bytes != expected.bytes {
        return Err(issue(
            "source_identity_mismatch",
            "source content differs from the frozen manifest",
        ));
    }
    Ok(())
}

// Hash and parse the SAME bounded bytes. Hashing a path and reopening it for
// parsing could otherwise admit a replacement made between those two steps.
fn read_artifact(expected: &Artifact) -> Checked<Value> {
    validate_identity(expected)?;
    if expected.bytes > MAX_JSON {
        return Err(issue("artifact_too_large", "JSON artifact exceeds64MiB"));
    }
    let bytes = input::read_bytes_bounded(Path::new(&expected.path), MAX_JSON)
        .map_err(|e| issue("artifact_unavailable", e))?;
    if bytes.len() as u64 != expected.bytes || digest(&bytes) != expected.sha256 {
        return Err(issue(
            "artifact_identity_mismatch",
            "artifact bytes or SHA256 differ from manifest",
        ));
    }
    serde_json::from_slice(&bytes).map_err(|e| issue("artifact_malformed", e))
}

fn same_input(value: &Value, expected: &Artifact) -> Checked<()> {
    let actual: Artifact =
        serde_json::from_value(value.clone()).map_err(|e| issue("input_identity_missing", e))?;
    validate_identity(&actual)?;
    // The frozen path is part of the source identity. To use a relocated corpus,
    // preserve provenance and generate a new manifest; do not silently relabel.
    if actual != *expected {
        return Err(issue(
            "wrong_input",
            "result source path/hash/bytes do not match this control",
        ));
    }
    Ok(())
}

// Independent bitwise implementation: do not trust receiver CRC booleans or
// call its table-based CRC routine. CRC-16/X-25, received FCS little-endian.
fn crc_x25(bytes: &[u8]) -> u16 {
    let mut crc = 0xffffu16;
    for byte in bytes {
        crc ^= u16::from(*byte);
        for _ in 0..8 {
            crc = if crc & 1 == 1 {
                (crc >> 1) ^ 0x8408
            } else {
                crc >> 1
            };
        }
    }
    crc ^ 0xffff
}

// Deliberately matches the project's narrow strict-UI contract: 2..10 valid
// AX.25 addresses, control=0x03, any PID, possibly empty information. This is
// NOT a validator for all AX.25 control types, CCSDS, mission identity or data.
fn valid_ui(pdu: &[u8]) -> bool {
    if !(16..=1022).contains(&pdu.len()) {
        return false;
    }
    for address in 0..10 {
        let start = address * 7;
        let Some(raw) = pdu.get(start..start + 7) else {
            return false;
        };
        if raw[6] & 0x60 != 0x60 || raw[..6].iter().any(|c| c & 1 != 0) {
            return false;
        }
        let mut padding = false;
        let mut letters = 0;
        for byte in &raw[..6] {
            match byte >> 1 {
                b' ' => padding = true,
                b'A'..=b'Z' | b'0'..=b'9' if !padding => letters += 1,
                _ => return false,
            }
        }
        if letters == 0 {
            return false;
        }
        if raw[6] & 1 != 0 {
            return address >= 1 && pdu.get(start + 7) == Some(&3) && pdu.get(start + 8).is_some();
        }
    }
    false
}

fn expected_set(case: &Case) -> Checked<BTreeSet<String>> {
    let mut result = BTreeSet::new();
    for pdu in &case.expected_pdu_hex {
        let bytes = hex::decode(pdu).map_err(|e| issue("invalid_expectation", e))?;
        if !valid_ui(&bytes) || hex::encode(&bytes) != *pdu || !result.insert(pdu.clone()) {
            return Err(issue(
                "invalid_expectation",
                "expected PDUs must be unique lowercase strict AX25 UI hex without FCS",
            ));
        }
    }
    if (case.kind == Kind::Positive) != !result.is_empty() {
        return Err(issue(
            "invalid_expectation",
            "positive controls require PDUs; negative controls require an empty set",
        ));
    }
    if case.expectation_note.trim().is_empty()
        || (case.kind == Kind::Negative)
            != (case.expectation_basis == ExpectationBasis::KnownNegativeInput)
    {
        return Err(issue(
            "invalid_expectation_provenance",
            "explain exact-input truth; negative controls require known_negative_input",
        ));
    }
    Ok(result)
}

fn parse_frames(value: &Value, object_frames: bool) -> Checked<BTreeSet<String>> {
    let array = value.as_array().ok_or_else(|| {
        issue(
            "frames_missing",
            "an explicit emitted-frame array is required, even when empty",
        )
    })?;
    let mut pdus = BTreeSet::new();
    for (index, item) in array.iter().enumerate() {
        let text = if object_frames {
            item["frame_with_fcs_hex"].as_str()
        } else {
            item.as_str()
        }
        .ok_or_else(|| {
            issue(
                "malformed_frame",
                format!("frame{index} lacks received-FCS hex"),
            )
        })?;
        let frame = hex::decode(text)
            .map_err(|e| issue("malformed_frame", format!("frame{index}: {e}")))?;
        if !(18..=1024).contains(&frame.len()) {
            return Err(issue(
                "malformed_frame",
                format!("frame{index} is outside strict UI frame size bounds"),
            ));
        }
        let (pdu, fcs) = frame.split_at(frame.len() - 2);
        if crc_x25(pdu).to_le_bytes() != fcs {
            return Err(issue(
                "invalid_received_fcs",
                format!("frame{index}: received FCS mismatch"),
            ));
        }
        if !valid_ui(pdu) {
            return Err(issue(
                "invalid_ax25_ui",
                format!("frame{index}: not strict AX25 UI; no telemetry claim assigned"),
            ));
        }
        if !pdus.insert(hex::encode(pdu)) {
            return Err(issue(
                "duplicate_emitted_frame",
                "result union contains duplicate PDU entries",
            ));
        }
    }
    Ok(pdus)
}

fn generic_iq_pdus(value: &Value, source: &Artifact) -> Checked<BTreeSet<String>> {
    if value["schema"] != "rust-generic-receiver-result-v1" {
        return Err(issue(
            "wrong_format",
            "expected rust-generic-receiver-result-v1",
        ));
    }
    same_input(&value["source"], source)?;
    if value["failed_window_count"].as_u64() != Some(0)
        || value["window_count"].as_u64().unwrap_or(0) == 0
    {
        return Err(issue(
            "incomplete_result",
            "generic receiver window accounting is incomplete",
        ));
    }
    let plan = &value["plan"];
    if !matches!(
        plan["format"].as_str(),
        Some("ci16_le" | "cf32_le" | "cf64_le")
    ) || !plan["sample_rate_hz"]
        .as_f64()
        .is_some_and(|v| v.is_finite() && v > 0.0)
    {
        return Err(issue(
            "unsupported_input_contract",
            "generic_iq requires explicit IQ encoding and positive sample rate",
        ));
    }
    let protocols = plan["protocols"]
        .as_object()
        .ok_or_else(|| issue("unsupported_protocol", "generic plan protocols missing"))?;
    if protocols.is_empty()
        || protocols
            .values()
            .any(|p| !matches!(p["type"].as_str(), Some("ax25" | "ax25_ccsds")))
    {
        return Err(issue(
            "unsupported_protocol",
            "this gate accepts only received-FCS AX25-family outer frames, not raw CCSDS or other protocols",
        ));
    }
    let hypotheses = plan["hypotheses"].as_array().ok_or_else(|| {
        issue(
            "invalid_receiver_plan",
            "generic waveform hypotheses missing",
        )
    })?;
    if hypotheses.is_empty()
        || hypotheses.iter().any(|h| {
            !h["protocol_id"]
                .as_str()
                .is_some_and(|id| protocols.contains_key(id))
        })
    {
        return Err(issue(
            "invalid_receiver_plan",
            "all generic hypotheses must reference declared AX25-family protocols",
        ));
    }
    let frames = value["frames"].as_array().ok_or_else(|| {
        issue(
            "frames_missing",
            "generic emitted-frame array required even when empty",
        )
    })?;
    let mut union = BTreeSet::new();
    let mut protocol_frames = BTreeSet::new();
    for frame in frames {
        let protocol = frame["protocol_id"]
            .as_str()
            .filter(|id| protocols.contains_key(*id))
            .ok_or_else(|| {
                issue(
                    "unsupported_protocol",
                    "generic frame protocol_id is not in the AX25-only plan",
                )
            })?;
        let full = frame["frame_hex"]
            .as_str()
            .ok_or_else(|| issue("malformed_frame", "generic frame_hex missing"))?;
        let pdus = parse_frames(&json!([full]), false)?;
        let pdu = pdus.into_iter().next().expect("one validated frame");
        // The generic receiver counts (protocol_id, frame) pairs. The same
        // outer AX.25 PDU can legitimately appear under ax25 and ax25_ccsds;
        // validate its full bytes but compare a single deduplicated PDU below.
        if !protocol_frames.insert((protocol.to_owned(), pdu.clone())) {
            return Err(issue(
                "duplicate_emitted_frame",
                "duplicate generic protocol/frame record",
            ));
        }
        union.insert(pdu);
    }
    if value["unique_frame_count"].as_u64() != Some(protocol_frames.len() as u64) {
        return Err(issue(
            "count_mismatch",
            "generic unique_frame_count differs from independently parsed protocol/frame pairs",
        ));
    }
    Ok(union)
}

fn frozen_mm_configuration() -> Value {
    json!({"gain_omega":std::f64::consts::TAU/100.0,"gain_mu":0.0625,"relative_limit":0.01,
        "interpolations":["Linear","Cubic"],"initial_phases":[0.0,0.25,0.5,0.75],
        "input_gains":[0.5,1.0,2.0],"threshold":0.0,"g3ruh_modes":[false,true]})
}

fn mm_clock_pdus(value: &Value, source: &Artifact) -> Checked<BTreeSet<String>> {
    if value["schema"] != "mm-clock-development-probe-v1" {
        return Err(issue(
            "wrong_format",
            "expected mm-clock-development-probe-v1",
        ));
    }
    same_input(&value["source"], source)?;
    same_input(&value["prepared"], source)?;
    if value["configuration"].is_null() {
        return Err(issue(
            "clock_configuration_unattested",
            "legacy MM result lacks recorded loop/deframer configuration and cannot qualify the frozen current control bank",
        ));
    }
    if value["configuration"] != frozen_mm_configuration()
        || value["packet_guided_clock"] != false
        || value["novel_algorithm"] != false
    {
        return Err(issue(
            "wrong_clock_configuration",
            "MM loop gains, relative limit, branch bank, threshold, G3RUH modes and signal-only/non-novel declarations must match the frozen component",
        ));
    }
    // A bounded identity declaration is required for new records. It is not a
    // cryptographic attestation of execution; the run provenance is owner-trusted.
    let executable: Artifact = serde_json::from_value(value["executable"].clone())
        .map_err(|e| issue("executable_identity_missing", e))?;
    validate_identity(&executable)?;
    if !value["conversion_command"].is_null()
        || value["sample_rate_hz"].as_u64() != Some(19200)
        || value["baud"].as_f64() != Some(9600.0)
    {
        return Err(issue(
            "unsupported_input_contract",
            "mm_clock control gate is explicitly19200Hz/9600baud pre-clock WAV with no implicit conversion; not an end-to-end IQ receiver",
        ));
    }
    let branches = value["branches"]
        .as_array()
        .ok_or_else(|| issue("incomplete_result", "M&M branch results missing"))?;
    if branches.len() != 24 {
        return Err(issue(
            "incomplete_result",
            "the complete frozen M&M bank requires24 branches",
        ));
    }
    let mut branch_keys = BTreeSet::new();
    let mut branch_union = BTreeSet::new();
    for branch in branches {
        if branch["status"] != "complete" || branch["symbols"].as_u64().unwrap_or(0) == 0 {
            return Err(issue(
                "incomplete_result",
                "every M&M branch must complete with a positive symbol count",
            ));
        }
        let interpolation = match branch["interpolation"].as_str() {
            Some("Linear") => 0,
            Some("Cubic") => 1,
            _ => return Err(issue("wrong_clock_bank", "unexpected M&M interpolator")),
        };
        let phase = [0.0, 0.25, 0.5, 0.75]
            .iter()
            .position(|v| Some(*v) == branch["phase"].as_f64())
            .ok_or_else(|| issue("wrong_clock_bank", "unexpected M&M initial phase"))?;
        let gain = [0.5, 1.0, 2.0]
            .iter()
            .position(|v| Some(*v) == branch["input_gain"].as_f64())
            .ok_or_else(|| issue("wrong_clock_bank", "unexpected M&M input gain"))?;
        if !branch_keys.insert((interpolation, phase, gain)) {
            return Err(issue(
                "wrong_clock_bank",
                "duplicate M&M branch means the frozen bank is incomplete",
            ));
        }
        branch_union.extend(parse_frames(&branch["frame_with_fcs_hex"], false)?);
    }
    let union = parse_frames(&value["frame_with_fcs_hex"], false)?;
    if union != branch_union {
        return Err(issue(
            "branch_union_mismatch",
            "M&M top-level frames differ from independently validated branch union",
        ));
    }
    if value["union_count"].as_u64() != Some(union.len() as u64) {
        return Err(issue(
            "count_mismatch",
            "M&M union_count differs from independently parsed PDU set",
        ));
    }
    Ok(union)
}

fn decode_artifact(run: &Run, format: Format, source: &Artifact) -> Checked<BTreeSet<String>> {
    let value = read_artifact(&run.artifact)?;
    if value["status"] != "complete" {
        return Err(issue(
            "incomplete_result",
            "receiver status must explicitly be complete",
        ));
    }
    if matches!(format, Format::GenericIq | Format::MmClock) && run.session_manifest.is_some() {
        return Err(issue(
            "unexpected_manifest",
            "session_manifest is progressive-only",
        ));
    }
    let (frames, object_frames, count) = match format {
        Format::NativeAudio => {
            if value["schema"] != "rust-native-audio-result-v1" {
                return Err(issue(
                    "wrong_format",
                    "expected rust-native-audio-result-v1",
                ));
            }
            if run.session_manifest.is_some() {
                return Err(issue(
                    "unexpected_manifest",
                    "session_manifest is progressive-only",
                ));
            }
            same_input(&value["source_input"], source)?;
            if value["failed_window_count"].as_u64() != Some(0)
                || value["window_count"].as_u64().unwrap_or(0) == 0
            {
                return Err(issue(
                    "incomplete_result",
                    "native result has failed or missing window accounting",
                ));
            }
            (&value["frames"], true, &value["unique_pdu_count"])
        }
        Format::Innovation => {
            if !matches!(
                value["schema"].as_str(),
                Some("innovation-audio-result-v1" | "innovation-audio-result-v2")
            ) {
                return Err(issue(
                    "wrong_format",
                    "expected innovation-audio-result-v1 orv2",
                ));
            }
            if run.session_manifest.is_some() {
                return Err(issue(
                    "unexpected_manifest",
                    "session_manifest is progressive-only",
                ));
            }
            same_input(&value["source"], source)?;
            if value["samples"].as_u64().unwrap_or(0) == 0
                || value["sample_rate_hz"].as_u64().unwrap_or(0) == 0
            {
                return Err(issue(
                    "incomplete_result",
                    "innovation sample accounting missing or zero",
                ));
            }
            (
                &value["report"]["union_full_frames"],
                false,
                &value["union_count"],
            )
        }
        Format::Progressive => {
            if value["schema"] != "progressive-audio-result-v1" {
                return Err(issue(
                    "wrong_format",
                    "expected progressive-audio-result-v1",
                ));
            }
            let total = value["total_tasks"].as_u64().unwrap_or(0);
            if value["complete"] != true
                || total == 0
                || value["completed_tasks"].as_u64() != Some(total)
                || value["window_count"].as_u64().unwrap_or(0) == 0
            {
                return Err(issue(
                    "incomplete_result",
                    "progressive bank is not completely processed",
                ));
            }
            let expected = run.session_manifest.as_ref().ok_or_else(|| {
                issue(
                    "session_manifest_missing",
                    "progressive input binding requires its frozen session manifest",
                )
            })?;
            let manifest: ProgressiveManifest = serde_json::from_value(read_artifact(expected)?)
                .map_err(|e| issue("session_manifest_malformed", e))?;
            if !["progressive-audio-session-v2","progressive-audio-session-v3"].contains(&manifest.schema.as_str())
                || !valid_hash(&manifest.executable_sha256)
                || (manifest.schema=="progressive-audio-session-v3"
                    && manifest.compute_identity.as_ref().is_none_or(|v| v["schema"]!="telemetry-compute-v1" || ![Some("cpu"),Some("cuda")].contains(&v["backend"].as_str())))
            {
                return Err(issue(
                    "session_manifest_malformed",
                    "unsupported progressive session schema/executable identity",
                ));
            }
            same_input(
                &serde_json::to_value(&manifest.audio.source).unwrap(),
                source,
            )?;
            let session_sha = digest(
                &serde_json::to_vec(&manifest)
                    .map_err(|e| issue("session_manifest_malformed", e))?,
            );
            if value["session_sha256"].as_str() != Some(&session_sha) {
                return Err(issue(
                    "wrong_session",
                    "result does not hash-bind the declared progressive session manifest",
                ));
            }
            (&value["frame_with_fcs_hex"], false, &value["union_count"])
        }
        Format::GenericIq => return generic_iq_pdus(&value, source),
        Format::MmClock => return mm_clock_pdus(&value, source),
    };
    let pdus = parse_frames(frames, object_frames)?;
    if count.as_u64() != Some(pdus.len() as u64) {
        return Err(issue(
            "count_mismatch",
            "reported unique count differs from independently parsed PDU set",
        ));
    }
    Ok(pdus)
}

fn audit(suite: &Suite) -> Value {
    let mut errors = vec![];
    let mut rows = vec![];
    let mut requirements = BTreeMap::new();
    let mut coverage: BTreeMap<String, (usize, usize)> = BTreeMap::new();
    if suite.schema != SCHEMA || suite.suite_id.trim().is_empty() {
        errors.push(issue(
            "invalid_suite",
            "supported schema and nonempty suite_id required",
        ));
    }
    if suite.required_receivers.is_empty() || suite.cases.is_empty() {
        errors.push(issue(
            "vacuous_suite",
            "nonempty receivers and controls required",
        ));
    }
    for receiver in &suite.required_receivers {
        if receiver.label.trim().is_empty()
            || requirements
                .insert(receiver.label.clone(), receiver.format)
                .is_some()
        {
            errors.push(issue(
                "invalid_receiver",
                "receiver labels must be nonempty and unique",
            ));
        }
        coverage.insert(receiver.label.clone(), (0, 0));
    }
    let mut ids = BTreeSet::new();
    let mut case_sources = BTreeMap::new();
    for case in &suite.cases {
        if case.id.trim().is_empty() || !ids.insert(case.id.clone()) {
            errors.push(issue(
                "invalid_case_id",
                "case IDs must be nonempty and unique",
            ));
        }
        if let Some(old) =
            case_sources.insert((case.source.sha256.clone(), case.source.bytes), case.kind)
        {
            if old != case.kind {
                errors.push(issue(
                    "conflicting_ground_truth",
                    "same input content is marked both positive and negative",
                ));
            }
        }
        if case.outputs.is_empty() {
            errors.push(issue(
                "no_receiver_outputs",
                format!("{} has no output artifacts", case.id),
            ));
        }
        let expectation = expected_set(case);
        let source_check = verify_source(&case.source);
        let mut run_labels = BTreeSet::new();
        for run in &case.outputs {
            let mut issues = vec![];
            let mut actual = BTreeSet::new();
            let mut extracted = false;
            if !run_labels.insert(run.receiver.clone()) {
                issues.push(issue(
                    "duplicate_receiver_output",
                    "one result per receiver per case required",
                ));
            }
            if let Err(e) = &expectation {
                issues.push(e.clone());
            }
            if let Err(e) = &source_check {
                issues.push(e.clone());
            }
            match requirements.get(&run.receiver) {
                None => issues.push(issue(
                    "unknown_receiver",
                    "output receiver is not required by the manifest",
                )),
                Some(format) => match decode_artifact(run, *format, &case.source) {
                    Ok(pdus) => {
                        actual = pdus;
                        extracted = true;
                    }
                    Err(e) => issues.push(e),
                },
            }
            let (missing, unexpected): (BTreeSet<String>, BTreeSet<String>) =
                if let Ok(expected) = &expectation {
                    (
                        expected.difference(&actual).cloned().collect(),
                        actual.difference(expected).cloned().collect(),
                    )
                } else {
                    (BTreeSet::new(), BTreeSet::new())
                };
            if extracted && (!missing.is_empty() || !unexpected.is_empty()) {
                issues.push(issue(
                    "pdu_set_mismatch",
                    "verified emitted PDU set differs from exact expected set",
                ));
            }
            let passed = issues.is_empty();
            if passed {
                if let Some(counts) = coverage.get_mut(&run.receiver) {
                    if case.kind == Kind::Positive {
                        counts.0 += 1;
                    } else {
                        counts.1 += 1;
                    }
                }
            }
            rows.push(json!({"case_id":case.id,"kind":case.kind,"receiver":run.receiver,
                "receiver_format":requirements.get(&run.receiver),
                "receiver_component_scope":if requirements.get(&run.receiver)==Some(&Format::MmClock){"clock/deframer on exact pre-clock audio; upstream frontend is external and not qualified here"}else{"declared receiver format; only received-FCS strict AX25 UI outer packets validated"},
                "passed":passed,"issues":issues,"source":case.source,"artifact":run.artifact,
                "session_manifest":run.session_manifest,"expected_pdu_hex":case.expected_pdu_hex,
                "verified_pdu_hex":if extracted{json!(actual)}else{Value::Null},
                "missing_expected_pdu_hex":if extracted{json!(missing)}else{Value::Null},
                "unexpected_pdu_hex":if extracted{json!(unexpected)}else{Value::Null},
                "expectation_basis":case.expectation_basis,"expectation_note":case.expectation_note}));
        }
    }
    let coverage_rows: Vec<_> = coverage
        .iter()
        .map(|(receiver, &(positive, negative))| {
            if positive == 0 || negative == 0 {
                errors.push(issue(
                    "required_control_coverage_missing",
                    format!("{receiver}: positive_passes={positive}, negative_passes={negative}"),
                ));
            }
            json!({"receiver":receiver,"positive_passes":positive,"negative_passes":negative})
        })
        .collect();
    let passed = errors.is_empty() && !rows.is_empty() && rows.iter().all(|v| v["passed"] == true);
    json!({"schema":"decoder-control-suite-audit-v1","suite_id":suite.suite_id,
        "control_suite_passed":passed,"publication_ready":false,"deployment_ready":false,
        "errors":errors,"receiver_coverage":coverage_rows,"runs":rows,
        "scope":"received-FCS strict AX25 UI exact-input control-set validation only",
        "trust_boundary":"Manifest expected PDUs and same-input truth provenance are supplied by the experiment owner; this audit does not establish their independence or signal origin.",
        "not_established":["held-out generalization","sensitivity advantage","false-positive rate bound","protocol-universal support","production deployment qualification"]})
}

fn main() {
    let mut args = std::env::args().skip(1);
    let mut manifest = None;
    let mut output = None;
    while let Some(arg) = args.next() {
        let slot = match arg.as_str() {
            "--manifest" => &mut manifest,
            "--output" => &mut output,
            "--help" | "-h" => {
                println!(
                    "decoder_readiness_audit --manifest CONTROL_SUITE.json --output NEW_AUDIT.json\nExit0: all controls pass; exit1: controls fail; exit2: invocation/output failure. Never asserts publication readiness."
                );
                return;
            }
            _ => {
                eprintln!("unknown argument: {arg}");
                std::process::exit(2);
            }
        };
        if slot.is_some() {
            eprintln!("duplicate argument: {arg}");
            std::process::exit(2);
        }
        *slot = args.next().map(PathBuf::from);
        if slot.is_none() {
            eprintln!("missing value for{arg}");
            std::process::exit(2);
        }
    }
    let (Some(manifest), Some(output)) = (manifest, output) else {
        eprintln!("--manifest and --output required");
        std::process::exit(2);
    };
    let result = input::read_bytes_bounded(&manifest, MAX_JSON)
        .and_then(|bytes| {
            let suite: Suite = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            let mut result = audit(&suite);
            result["manifest"] = json!({"path":manifest.display().to_string(),"sha256":digest(&bytes),"bytes":bytes.len()});
            Ok(result)
        });
    let report = result.unwrap_or_else(|error| {
        json!({"schema":"decoder-control-suite-audit-v1",
        "control_suite_passed":false,"publication_ready":false,"deployment_ready":false,
        "errors":[{"code":"manifest_unreadable_or_invalid","detail":error}]})
    });
    if let Err(error) = input::write_json_new(&output, &report) {
        eprintln!("cannot publish audit without overwriting existing data: {error}");
        std::process::exit(2);
    }
    println!(
        "{}",
        json!({"output":output,"control_suite_passed":report["control_suite_passed"],"publication_ready":false})
    );
    if report["control_suite_passed"] != true {
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    fn artifact(path: &Path) -> Artifact {
        let bytes = fs::read(path).unwrap();
        Artifact {
            path: path.display().to_string(),
            sha256: digest(&bytes),
            bytes: bytes.len() as u64,
        }
    }
    fn write(path: &Path, value: &Value) -> Artifact {
        fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
        artifact(path)
    }
    fn pdu() -> Vec<u8> {
        hex::decode("82a0a4a64040609c60868298986103f048454c4c4f").unwrap()
    }
    fn framed(bytes: &[u8]) -> String {
        let mut frame = bytes.to_vec();
        frame.extend_from_slice(&crc_x25(bytes).to_le_bytes());
        hex::encode(frame)
    }
    fn result(format: Format, source: &Artifact, frames: Vec<String>) -> Value {
        match format {
            Format::NativeAudio => {
                json!({"schema":"rust-native-audio-result-v1","status":"complete","source_input":source,
                "failed_window_count":0,"window_count":1,"unique_pdu_count":frames.len(),
                "frames":frames.iter().map(|f|json!({"frame_with_fcs_hex":f})).collect::<Vec<_>>()})
            }
            Format::Innovation => {
                json!({"schema":"innovation-audio-result-v2","status":"complete","source":source,"samples":48000,
                "sample_rate_hz":48000,"union_count":frames.len(),"report":{"union_full_frames":frames}})
            }
            Format::Progressive => {
                json!({"schema":"progressive-audio-result-v1","status":"complete","complete":true,
                "total_tasks":8,"completed_tasks":8,"window_count":1,"union_count":frames.len(),"frame_with_fcs_hex":frames})
            }
            Format::GenericIq => {
                json!({"schema":"rust-generic-receiver-result-v1","status":"complete","source":source,
                "failed_window_count":0,"window_count":1,"unique_frame_count":frames.len(),
                "plan":{"format":"ci16_le","sample_rate_hz":57600,"protocols":{"ax25":{"type":"ax25","g3ruh_modes":[false,true]}},"hypotheses":[{"protocol_id":"ax25"}]},
                "frames":frames.iter().map(|f|json!({"frame_hex":f,"protocol_id":"ax25"})).collect::<Vec<_>>()})
            }
            Format::MmClock => {
                let mut branches = vec![];
                for interpolation in ["Linear", "Cubic"] {
                    for phase in [0.0, 0.25, 0.5, 0.75] {
                        for gain in [0.5, 1.0, 2.0] {
                            branches.push(json!({"interpolation":interpolation,"phase":phase,"input_gain":gain,
                                "status":"complete","symbols":9600,"frame_with_fcs_hex":frames}));
                        }
                    }
                }
                json!({"schema":"mm-clock-development-probe-v1","status":"complete","source":source,"prepared":source,
                    "sample_rate_hz":19200,"baud":9600.0,"conversion_command":null,
                    "configuration":frozen_mm_configuration(),"executable":source,
                    "packet_guided_clock":false,"novel_algorithm":false,
                    "union_count":frames.len(),"frame_with_fcs_hex":frames,"branches":branches})
            }
        }
    }
    fn fixture(format: Format) -> (tempfile::TempDir, Suite) {
        let tmp = tempfile::tempdir().unwrap();
        let mut cases = vec![];
        for positive in [true, false] {
            let id = if positive { "positive" } else { "negative" };
            let source_path = tmp.path().join(format!("{id}.source"));
            fs::write(&source_path, id.as_bytes()).unwrap();
            let source = artifact(&source_path);
            let frames = if positive {
                vec![framed(&pdu())]
            } else {
                vec![]
            };
            let mut decoded = result(format, &source, frames);
            let session_manifest = if format == Format::Progressive {
                let session = ProgressiveManifest {
                    schema: "progressive-audio-session-v2".into(),
                    compute_identity: None,
                    audio: PreparedMetadata {
                        source: source.clone(),
                        wav: source.clone(),
                        sample_rate: 48000,
                        samples: 48000,
                        conversion_command: None,
                    },
                    executable_sha256: "a".repeat(64),
                    policy: json!({"version":2,"baud":9600}),
                };
                decoded["session_sha256"] = json!(digest(&serde_json::to_vec(&session).unwrap()));
                Some(write(
                    &tmp.path().join(format!("{id}.session.json")),
                    &serde_json::to_value(session).unwrap(),
                ))
            } else {
                None
            };
            let output = write(&tmp.path().join(format!("{id}.json")), &decoded);
            cases.push(Case {
                id: id.into(),
                kind: if positive {
                    Kind::Positive
                } else {
                    Kind::Negative
                },
                source,
                expected_pdu_hex: if positive {
                    vec![hex::encode(pdu())]
                } else {
                    vec![]
                },
                expectation_basis: if positive {
                    ExpectationBasis::SyntheticExactInput
                } else {
                    ExpectationBasis::KnownNegativeInput
                },
                expectation_note: "Synthetic parser fixture, not a real DSP performance experiment"
                    .into(),
                outputs: vec![Run {
                    receiver: "test".into(),
                    artifact: output,
                    session_manifest,
                }],
            });
        }
        (
            tmp,
            Suite {
                schema: SCHEMA.into(),
                suite_id: "unit-fixture".into(),
                required_receivers: vec![RequiredReceiver {
                    label: "test".into(),
                    format,
                }],
                cases,
            },
        )
    }
    fn mutate(suite: &mut Suite, f: impl FnOnce(&mut Value)) {
        let run = &mut suite.cases[0].outputs[0];
        let mut value = read_artifact(&run.artifact).unwrap();
        f(&mut value);
        run.artifact = write(Path::new(&run.artifact.path), &value);
    }
    fn has_code(value: &Value, code: &str) -> bool {
        value["errors"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["code"] == code)
            || value["runs"].as_array().unwrap().iter().any(|r| {
                r["issues"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|e| e["code"] == code)
            })
    }
    #[test]
    fn independent_crc_known_check_vector() {
        assert_eq!(crc_x25(b"123456789"), 0x906e);
    }
    #[test]
    fn all_five_actual_result_shapes_pass_but_never_publication() {
        for format in [
            Format::NativeAudio,
            Format::Innovation,
            Format::Progressive,
            Format::GenericIq,
            Format::MmClock,
        ] {
            let (_tmp, suite) = fixture(format);
            let value = audit(&suite);
            assert_eq!(value["control_suite_passed"], true, "{value}");
            assert_eq!(value["publication_ready"], false);
            assert_eq!(value["deployment_ready"], false);
        }
    }
    #[test]
    fn artifact_mutation_without_rehash_fails() {
        let (_tmp, suite) = fixture(Format::Innovation);
        let path = &suite.cases[0].outputs[0].artifact.path;
        let mut bytes = fs::read(path).unwrap();
        bytes.push(b' ');
        fs::write(path, bytes).unwrap();
        assert!(has_code(&audit(&suite), "artifact_identity_mismatch"));
    }
    #[test]
    fn source_mutation_and_size_mismatch_fail() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        fs::write(&suite.cases[0].source.path, b"tampered").unwrap();
        assert!(has_code(&audit(&suite), "source_identity_mismatch"));
        suite.cases[1].outputs[0].artifact.bytes += 1;
        assert!(has_code(&audit(&suite), "artifact_identity_mismatch"));
    }
    #[test]
    fn missing_artifact_is_not_empty_success() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        suite.cases[0].outputs[0].artifact.path.push_str(".missing");
        let value = audit(&suite);
        assert!(has_code(&value, "artifact_unavailable"));
        assert!(value["runs"][0]["verified_pdu_hex"].is_null());
    }
    #[test]
    fn bad_received_fcs_with_rehashed_artifact_fails() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        let mut bad = hex::decode(framed(&pdu())).unwrap();
        *bad.last_mut().unwrap() ^= 1;
        mutate(&mut suite, |v| {
            v["report"]["union_full_frames"] = json!([hex::encode(bad)])
        });
        assert!(has_code(&audit(&suite), "invalid_received_fcs"));
    }
    #[test]
    fn valid_crc_non_ui_is_rejected() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        let mut bad = pdu();
        bad[14] = 0x00;
        mutate(&mut suite, |v| {
            v["report"]["union_full_frames"] = json!([framed(&bad)])
        });
        assert!(has_code(&audit(&suite), "invalid_ax25_ui"));
    }
    #[test]
    fn partial_or_fake_complete_progressive_fails() {
        let (_tmp, mut suite) = fixture(Format::Progressive);
        mutate(&mut suite, |v| v["complete"] = json!(false));
        assert!(has_code(&audit(&suite), "incomplete_result"));
        mutate(&mut suite, |v| {
            v["complete"] = json!(true);
            v["completed_tasks"] = json!(7);
        });
        assert!(has_code(&audit(&suite), "incomplete_result"));
    }
    #[test]
    fn incomplete_innovation_and_failed_native_window_fail() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        mutate(&mut suite, |v| v["status"] = json!("partial"));
        assert!(has_code(&audit(&suite), "incomplete_result"));
        let (_tmp2, mut suite) = fixture(Format::NativeAudio);
        mutate(&mut suite, |v| v["failed_window_count"] = json!(1));
        assert!(has_code(&audit(&suite), "incomplete_result"));
    }
    #[test]
    fn wrong_source_path_hash_and_size_fail() {
        for key in ["path", "sha256", "bytes"] {
            let (_tmp, mut suite) = fixture(Format::Innovation);
            mutate(&mut suite, |v| {
                v["source"][key] = match key {
                    "path" => json!("/different/input.ogg"),
                    "sha256" => json!("f".repeat(64)),
                    _ => json!(999),
                }
            });
            assert!(has_code(&audit(&suite), "wrong_input"));
        }
    }
    #[test]
    fn wrong_or_missing_session_binding_fails() {
        let (_tmp, mut suite) = fixture(Format::Progressive);
        mutate(&mut suite, |v| v["session_sha256"] = json!("b".repeat(64)));
        assert!(has_code(&audit(&suite), "wrong_session"));
        suite.cases[0].outputs[0].session_manifest = None;
        assert!(has_code(&audit(&suite), "session_manifest_missing"));
    }
    #[test]
    fn missing_and_unexpected_pdus_are_reported_separately() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        let mut other = pdu();
        other.push(b'!');
        mutate(&mut suite, |v| {
            v["report"]["union_full_frames"] = json!([framed(&other)])
        });
        let value = audit(&suite);
        assert!(has_code(&value, "pdu_set_mismatch"));
        assert_eq!(
            value["runs"][0]["missing_expected_pdu_hex"],
            json!([hex::encode(pdu())])
        );
        assert_eq!(
            value["runs"][0]["unexpected_pdu_hex"],
            json!([hex::encode(other)])
        );
    }
    #[test]
    fn no_empty_suite_or_missing_negative_receiver_can_pass() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        suite.cases.pop();
        assert!(has_code(
            &audit(&suite),
            "required_control_coverage_missing"
        ));
        suite.cases.clear();
        suite.required_receivers.clear();
        assert!(has_code(&audit(&suite), "vacuous_suite"));
    }
    #[test]
    fn positive_empty_expectation_and_bad_truth_basis_fail() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        suite.cases[0].expected_pdu_hex.clear();
        assert!(has_code(&audit(&suite), "invalid_expectation"));
        suite.cases[1].expectation_basis = ExpectationBasis::IndependentExactInput;
        assert!(has_code(&audit(&suite), "invalid_expectation_provenance"));
    }
    #[test]
    fn missing_array_and_count_only_cannot_pass() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        mutate(&mut suite, |v| {
            v["report"]
                .as_object_mut()
                .unwrap()
                .remove("union_full_frames");
        });
        assert!(has_code(&audit(&suite), "frames_missing"));
    }
    #[test]
    fn false_count_and_duplicate_union_fail() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        mutate(&mut suite, |v| v["union_count"] = json!(9));
        assert!(has_code(&audit(&suite), "count_mismatch"));
        mutate(&mut suite, |v| {
            v["report"]["union_full_frames"] = json!([framed(&pdu()), framed(&pdu())])
        });
        assert!(has_code(&audit(&suite), "duplicate_emitted_frame"));
    }
    #[test]
    fn every_required_receiver_needs_both_control_types() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        suite.required_receivers.push(RequiredReceiver {
            label: "untested".into(),
            format: Format::NativeAudio,
        });
        assert!(has_code(
            &audit(&suite),
            "required_control_coverage_missing"
        ));
    }
    #[test]
    fn wrong_schema_and_unknown_manifest_fields_fail() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        mutate(&mut suite, |v| v["schema"] = json!("generic-count-report"));
        assert!(has_code(&audit(&suite), "wrong_format"));
        let mut value = serde_json::to_value(suite).unwrap();
        value["ignore_errors"] = json!(true);
        assert!(serde_json::from_value::<Suite>(value).is_err());
    }
    #[test]
    fn conflicting_source_truth_and_duplicate_labels_fail() {
        let (_tmp, mut suite) = fixture(Format::Innovation);
        suite.cases[1].source = suite.cases[0].source.clone();
        suite
            .required_receivers
            .push(suite.required_receivers[0].clone());
        let value = audit(&suite);
        assert!(has_code(&value, "conflicting_ground_truth"));
        assert!(has_code(&value, "invalid_receiver"));
    }
    #[test]
    fn malformed_hex_short_frames_and_invalid_addresses_fail() {
        for bad in ["zz".to_owned(), "1234".to_owned()] {
            assert!(parse_frames(&json!([bad]), false).is_err());
        }
        let mut address = pdu();
        address[0] |= 1;
        assert!(!valid_ui(&address));
        let mut address = pdu();
        address[6] &= !0x60;
        assert!(!valid_ui(&address));
        let mut address = pdu();
        address[13] &= !1;
        assert!(!valid_ui(&address));
    }

    #[test]
    fn generic_rejects_non_ax25_even_when_that_protocol_emits_nothing() {
        let (_tmp, mut suite) = fixture(Format::GenericIq);
        mutate(&mut suite, |v| {
            v["plan"]["protocols"]["ccsds"] = json!({"type":"raw_ccsds"})
        });
        assert!(has_code(&audit(&suite), "unsupported_protocol"));
        mutate(&mut suite, |v| v["plan"]["protocols"] = json!({}));
        assert!(has_code(&audit(&suite), "unsupported_protocol"));
    }

    #[test]
    fn generic_frame_and_hypothesis_ids_must_be_bound_to_the_plan() {
        let (_tmp, mut suite) = fixture(Format::GenericIq);
        mutate(&mut suite, |v| {
            v["frames"][0]["protocol_id"] = json!("not-declared")
        });
        assert!(has_code(&audit(&suite), "unsupported_protocol"));
        mutate(&mut suite, |v| {
            v["plan"]["hypotheses"][0]["protocol_id"] = json!("not-declared")
        });
        assert!(has_code(&audit(&suite), "invalid_receiver_plan"));
    }

    #[test]
    fn generic_iq_cannot_silently_qualify_audio_or_unknown_encoding() {
        for format in ["audio", "metadata", "unrecognized"] {
            let (_tmp, mut suite) = fixture(Format::GenericIq);
            mutate(&mut suite, |v| v["plan"]["format"] = json!(format));
            assert!(has_code(&audit(&suite), "unsupported_input_contract"));
        }
    }

    #[test]
    fn generic_protocol_pair_count_and_pdu_dedup_are_distinct() {
        let (_tmp, mut suite) = fixture(Format::GenericIq);
        mutate(&mut suite, |v| {
            v["plan"]["protocols"]["ax25_ccsds"] =
                json!({"type":"ax25_ccsds","g3ruh_modes":[true]});
            let mut second = v["frames"][0].clone();
            second["protocol_id"] = json!("ax25_ccsds");
            v["frames"].as_array_mut().unwrap().push(second);
            v["unique_frame_count"] = json!(2);
        });
        let value = audit(&suite);
        assert_eq!(value["control_suite_passed"], true, "{value}");
        assert_eq!(
            value["runs"][0]["verified_pdu_hex"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        mutate(&mut suite, |v| v["unique_frame_count"] = json!(1));
        assert!(has_code(&audit(&suite), "count_mismatch"));
    }

    #[test]
    fn generic_iq_received_fcs_is_independently_checked() {
        let (_tmp, mut suite) = fixture(Format::GenericIq);
        let mut bad = hex::decode(framed(&pdu())).unwrap();
        *bad.last_mut().unwrap() ^= 1;
        mutate(&mut suite, |v| {
            v["frames"][0]["frame_hex"] = json!(hex::encode(bad))
        });
        assert!(has_code(&audit(&suite), "invalid_received_fcs"));
    }

    #[test]
    fn mm_requires_all24_complete_unique_frozen_branches() {
        let (_tmp, mut suite) = fixture(Format::MmClock);
        mutate(&mut suite, |v| v["branches"][0]["status"] = json!("failed"));
        assert!(has_code(&audit(&suite), "incomplete_result"));
        mutate(&mut suite, |v| v["branches"][0] = v["branches"][1].clone());
        assert!(has_code(&audit(&suite), "wrong_clock_bank"));
        mutate(&mut suite, |v| {
            v["branches"].as_array_mut().unwrap().pop();
        });
        assert!(has_code(&audit(&suite), "incomplete_result"));
    }

    #[test]
    fn mm_checks_branch_frames_not_only_claimed_top_level_union() {
        let (_tmp, mut suite) = fixture(Format::MmClock);
        let mut bad = hex::decode(framed(&pdu())).unwrap();
        *bad.last_mut().unwrap() ^= 1;
        mutate(&mut suite, |v| {
            v["branches"][0]["frame_with_fcs_hex"] = json!([hex::encode(bad)])
        });
        assert!(has_code(&audit(&suite), "invalid_received_fcs"));
        mutate(&mut suite, |v| {
            for branch in v["branches"].as_array_mut().unwrap() {
                branch["frame_with_fcs_hex"] = json!([]);
            }
        });
        assert!(has_code(&audit(&suite), "branch_union_mismatch"));
    }

    #[test]
    fn mm_rejects_changed_gain_or_input_geometry() {
        let (_tmp, mut suite) = fixture(Format::MmClock);
        mutate(&mut suite, |v| v["branches"][0]["input_gain"] = json!(4.0));
        assert!(has_code(&audit(&suite), "wrong_clock_bank"));
        mutate(&mut suite, |v| v["sample_rate_hz"] = json!(48000));
        assert!(has_code(&audit(&suite), "unsupported_input_contract"));
    }

    #[test]
    fn mm_prepared_source_binding_and_count_cannot_be_faked() {
        let (_tmp, mut suite) = fixture(Format::MmClock);
        mutate(&mut suite, |v| v["union_count"] = json!(12));
        assert!(has_code(&audit(&suite), "count_mismatch"));
        mutate(&mut suite, |v| {
            v["prepared"]["sha256"] = json!("e".repeat(64))
        });
        assert!(has_code(&audit(&suite), "wrong_input"));
    }

    #[test]
    fn mm_audit_explicitly_labels_component_not_end_to_end_frontend() {
        let (_tmp, suite) = fixture(Format::MmClock);
        let value = audit(&suite);
        assert_eq!(value["runs"][0]["receiver_format"], "mm_clock");
        assert!(
            value["runs"][0]["receiver_component_scope"]
                .as_str()
                .unwrap()
                .contains("upstream frontend is external")
        );
        assert_eq!(value["publication_ready"], false);
    }

    #[test]
    fn mm_rejects_every_changed_fixed_configuration_field() {
        for (key, replacement) in [
            ("gain_omega", json!(0.0)),
            ("gain_mu", json!(0.2)),
            ("relative_limit", json!(0.1)),
            ("threshold", json!(1.0)),
            ("g3ruh_modes", json!([false])),
            ("initial_phases", json!([0.0])),
            ("input_gains", json!([1.0])),
            ("interpolations", json!(["Linear"])),
        ] {
            let (_tmp, mut suite) = fixture(Format::MmClock);
            mutate(&mut suite, |v| v["configuration"][key] = replacement);
            assert!(
                has_code(&audit(&suite), "wrong_clock_configuration"),
                "{key}"
            );
        }
    }

    #[test]
    fn mm_legacy_configuration_absence_cannot_pass_current_qualification() {
        let (_tmp, mut suite) = fixture(Format::MmClock);
        mutate(&mut suite, |v| {
            v.as_object_mut().unwrap().remove("configuration");
        });
        assert!(has_code(&audit(&suite), "clock_configuration_unattested"));
    }

    #[test]
    fn mm_rejects_packet_guided_or_missing_signal_only_declaration() {
        for flag in ["packet_guided_clock", "novel_algorithm"] {
            let (_tmp, mut suite) = fixture(Format::MmClock);
            mutate(&mut suite, |v| v[flag] = json!(true));
            assert!(has_code(&audit(&suite), "wrong_clock_configuration"));
            mutate(&mut suite, |v| {
                v.as_object_mut().unwrap().remove(flag);
            });
            assert!(has_code(&audit(&suite), "wrong_clock_configuration"));
        }
    }

    #[test]
    fn mm_new_records_require_explicit_executable_identity() {
        let (_tmp, mut suite) = fixture(Format::MmClock);
        mutate(&mut suite, |v| {
            v.as_object_mut().unwrap().remove("executable");
        });
        assert!(has_code(&audit(&suite), "executable_identity_missing"));
    }

    #[test]
    #[ignore = "read-only compatibility check of current optional CANVAS5122 IQ and preclock artifacts"]
    fn preserved_generic_iq_and_legacy_mm_match_only_at_frame_evidence_tier() {
        let mut sets = vec![];
        for (format, path) in [
            (
                Format::GenericIq,
                "work/decoder-readiness-20260911/canvas5122-generic-iq/result.json",
            ),
            (
                Format::MmClock,
                "work/decoder-readiness-20260911/mm-preclock/result.json",
            ),
        ] {
            let artifact = artifact(&Path::new(path).canonicalize().unwrap());
            let result = read_artifact(&artifact).unwrap();
            let source: Artifact = serde_json::from_value(result["source"].clone()).unwrap();
            verify_source(&source).unwrap();
            let run = Run {
                receiver: "preserved-adapter-test".into(),
                artifact,
                session_manifest: None,
            };
            if format == Format::MmClock {
                // Old output is valuable packet evidence but cannot attest the
                // loop configuration now required for a full control pass.
                let error = decode_artifact(&run, format, &source).unwrap_err();
                assert_eq!(error.code, "clock_configuration_unattested");
                sets.push(parse_frames(&result["frame_with_fcs_hex"], false).unwrap());
            } else {
                sets.push(decode_artifact(&run, format, &source).unwrap());
            }
        }
        assert_eq!(sets[0].len(), 1);
        assert_eq!(sets[0], sets[1]);
        // This is cross-format consistency, not proof of independent on-air
        // truth or Rust-only end-to-end reception; the input representations differ.
    }

    #[test]
    #[ignore = "read-only adapter integration against optional preserved public14967362 artifacts"]
    fn preserved_real_progressive_artifact_has_valid_session_and_received_frames() {
        let base = Path::new("work/innovation-field-20260910-v1/progressive-14967362");
        let manifest = artifact(&base.join("manifest.json").canonicalize().unwrap());
        let parsed: ProgressiveManifest =
            serde_json::from_value(read_artifact(&manifest).unwrap()).unwrap();
        verify_source(&parsed.audio.source).unwrap();
        let run = Run {
            receiver: "preserved-progressive-format-integration-only".into(),
            artifact: artifact(&base.join("result.json").canonicalize().unwrap()),
            session_manifest: Some(manifest),
        };
        let frames = decode_artifact(&run, Format::Progressive, &parsed.audio.source).unwrap();
        assert!(!frames.is_empty());
        // This tests adapter compatibility/FCS, not independently specified
        // recovery truth: it deliberately does not create a passing suite.
    }
}
