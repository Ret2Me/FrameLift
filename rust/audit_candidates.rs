//! Evidence ledger for the frozen OGG experiment, not a new decoding experiment.
//! Invoked as a child of audit to reuse independent received-FCS verification.
use super::*;
use crate::{formats, input, protocol};

fn pdu_set(value: &Value) -> AuditResult<BTreeSet<Vec<u8>>> {
    let values = array(value, "pdus")?;
    let set: BTreeSet<Vec<u8>> = values
        .iter()
        .map(|p| hex::decode(p.as_str().ok_or("non-string PDU")?).map_err(|e| e.to_string()))
        .collect::<AuditResult<_>>()?;
    require(set.len() == values.len(), "duplicate PDU in declared set")?;
    require(
        integer(value, "count")? == set.len() as u64,
        "declared PDU count mismatch",
    )?;
    require(
        integer(value, "bytes")? == set.iter().map(|p| p.len() as u64).sum::<u64>(),
        "declared PDU bytes mismatch",
    )?;
    require(
        set.iter().all(|p| protocol::parse_ax25_ui(p).is_some()),
        "invalid AX25 UI PDU",
    )?;
    Ok(set)
}

fn address_pair(pdu: &[u8]) -> AuditResult<(String, String)> {
    let parsed = protocol::parse_ax25_ui(pdu).ok_or("invalid AX25 UI addresses")?;
    Ok((
        format!("{}-{}", parsed.source.callsign, parsed.source.ssid),
        format!(
            "{}-{}",
            parsed.destination.callsign, parsed.destination.ssid
        ),
    ))
}

fn verified_archive(campaign: &Campaign, row: &Value) -> AuditResult<BTreeSet<Vec<u8>>> {
    let refs = &row["references"];
    require(
        refs["complete"] == true && array(refs, "failures")?.is_empty(),
        "incomplete references cannot establish archive absence",
    )?;
    let objects = array(refs, "objects")?;
    require(
        integer(refs, "listed_count")? == objects.len() as u64,
        "reference object list is incomplete",
    )?;
    let mut result = BTreeSet::new();
    for object in objects {
        let path = verify_identity(&campaign.root, object)?;
        let bytes = input::read_bytes_bounded(&path, MAX_JSON_BYTES)?;
        if object["strict_ax25_ui_structure"] == true {
            require(
                bytes == decode_hex(object, "payload_hex")?,
                "archive payload differs from original downloaded bytes",
            )?;
            require(
                protocol::parse_ax25_ui(&bytes).is_some(),
                "invalid archive AX25 UI structure",
            )?;
            result.insert(bytes);
        }
    }
    if row["status"] == "complete" {
        require(
            result == pdu_set(&row["comparison"]["counts"]["archive"])?,
            "archive comparison set differs from downloaded evidence",
        )?;
    }
    Ok(result)
}

fn verified_baseline(campaign: &Campaign, row: &Value) -> AuditResult<BTreeSet<Vec<u8>>> {
    let attempt = inside(&campaign.root, Path::new(text(row, "attempt")?))?;
    for name in ["baseline/result.json", "baseline/frames.kiss"] {
        let wanted = attempt.join(name);
        let identity = array(row, "artifacts")?
            .iter()
            .find(|item| {
                item["path"]
                    .as_str()
                    .is_some_and(|s| Path::new(s) == wanted)
            })
            .ok_or_else(|| format!("baseline artifact not committed: {name}"))?;
        require(
            verify_identity(&campaign.root, identity)? == wanted,
            "baseline artifact mismatch",
        )?;
    }
    let baseline = read_json(&attempt.join("baseline/result.json"))?;
    require(
        baseline["completed"] == true
            && baseline["timed_out"] == false
            && baseline["returncode"] == 0,
        "baseline did not complete",
    )?;
    require(
        sha_string(&baseline, "input_sha256")?
            == sha_string(&row["comparison"], "same_audio_sha256")?,
        "baseline uses different audio",
    )?;
    let kiss = formats::parse_kiss(&input::read_bytes_bounded(
        &attempt.join("baseline/frames.kiss"),
        MAX_JSON_BYTES,
    )?)?;
    require(
        kiss.malformed_records == 0,
        "malformed baseline KISS records",
    )?;
    let set: BTreeSet<_> = kiss
        .data_frames
        .into_iter()
        .map(|f| f.payload)
        .filter(|p| protocol::parse_ax25_ui(p).is_some())
        .collect();
    require(
        set == pdu_set(&row["comparison"]["counts"]["baseline_same_audio"])?,
        "baseline comparison set differs from original KISS evidence",
    )?;
    Ok(set)
}

/// Verify all original evidence and an existing secondary Rust replay, then
/// inventory archive-absent PDUs and baseline omissions. Metadata agreement is
/// explicitly not promoted to authenticated mission attribution.
pub fn inventory(reference_summary: &Path, replay_root: &Path) -> AuditResult<Value> {
    let campaign = load_campaign(reference_summary)?;
    let root = replay_root.canonicalize().map_err(|e| e.to_string())?;
    let rows = array(&campaign.summary, "observations")?;
    let mut archive_global = BTreeSet::new();
    let mut archives = BTreeMap::new();
    for row in rows {
        let archive = verified_archive(&campaign, row)?;
        archive_global.extend(archive.iter().cloned());
        archives.insert(integer(row, "observation_id")?, archive);
    }
    let archive_addresses: BTreeSet<_> = archive_global
        .iter()
        .map(|p| address_pair(p))
        .collect::<AuditResult<_>>()?;
    let mut global_candidates: BTreeMap<Vec<u8>, Value> = BTreeMap::new();
    let mut baseline_global = BTreeSet::new();
    let mut observations = Vec::new();
    let mut missing = Vec::new();
    let mut local_extra_count = 0;
    let mut both_extra_count = 0;
    for row in rows.iter().filter(|r| r["status"] == "complete") {
        let reference = verify_reference(&campaign, row)?;
        let original_plan = read_json(&Path::new(text(row, "attempt")?).join("native.plan.json"))?;
        require(
            original_plan["decoder"] == "fast"
                && original_plan["reference_bytes_used_for_search"] == false,
            "candidate inventory is restricted to the frozen non-repair, reference-free decoder",
        )?;
        let replay_dir = inside(&root, &root.join(reference.id.to_string()))?;
        let replay = compare_one(&reference, &replay_dir)?;
        require(
            replay["pass"] == true,
            format!("secondary replay differs for observation {}", reference.id),
        )?;
        let archive = &archives[&reference.id];
        let baseline = verified_baseline(&campaign, row)?;
        baseline_global.extend(baseline.iter().cloned());
        let native: BTreeSet<_> = reference.frames.keys().cloned().collect();
        let local_extra: BTreeSet<_> = native.difference(archive).cloned().collect();
        let both_extra: BTreeSet<_> = local_extra.difference(&baseline).cloned().collect();
        let baseline_only: BTreeSet<_> = baseline.difference(&native).cloned().collect();
        for (name, computed) in [
            ("native_new_vs_archive", &local_extra),
            ("native_new_vs_archive_and_baseline", &both_extra),
            ("baseline_only_vs_native", &baseline_only),
        ] {
            require(
                *computed == pdu_set(&row["comparison"]["counts"][name])?,
                format!("{name} set mismatch"),
            )?;
        }
        local_extra_count += local_extra.len();
        both_extra_count += both_extra.len();
        for pdu in baseline_only {
            missing.push(
                json!({"observation_id":reference.id, "payload_hex":hex::encode(&pdu),
                "payload_sha256":hex::encode(Sha256::digest(&pdu)), "bytes":pdu.len(),
                "source_evidence":"hash-verified baseline KISS; FCS stripped by baseline"}),
            );
        }
        for (pdu, frame) in reference
            .frames
            .iter()
            .filter(|(pdu, _)| !archive_global.contains(*pdu))
        {
            let pair = address_pair(pdu)?;
            let parsed = protocol::parse_ax25_ui(pdu).ok_or("invalid candidate AX25 UI")?;
            let ccsds = protocol::parse_ccsds_space_packet(&parsed.information, true);
            let entry = global_candidates.entry(pdu.clone()).or_insert_with(|| {
                json!({
                    "payload_sha256":frame.sha256,"payload_hex":hex::encode(pdu),
                    "frame_with_received_fcs_hex":hex::encode(&frame.full),"bytes":pdu.len(),
                    "ax25_source":pair.0,"ax25_destination":pair.1,
                    "address_pair_present_in_archive":archive_addresses.contains(&pair),
                    "independent_received_fcs_verified":true,"secondary_rust_replay_verified":true,
                    "repair_used":false,"origin_attribution":"unverified",
                    "encapsulated_ccsds_primary_header":ccsds,
                    "ccsds_header_is_structure_only_not_independent_integrity":true,
                    "observations":[]
                })
            });
            entry["observations"].as_array_mut().ok_or("invalid ledger entry")?.push(json!({
                "observation_id":reference.id,"same_audio_sha256":reference.native["input"]["sha256"],
                "also_in_same_audio_baseline":baseline.contains(pdu),"provenance":frame.provenance,
                "secondary_result_sha256":replay["result_sha256"]
            }));
        }
        observations.push(json!({"observation_id":reference.id,"replay_pass":true,
            "native_pdus":native.len(),"archive_absent_local":local_extra.len(),
            "archive_and_baseline_absent_local":both_extra.len(),"replay_result_sha256":replay["result_sha256"]}));
    }
    let candidate_keys: BTreeSet<_> = global_candidates.keys().cloned().collect();
    require(
        candidate_keys == pdu_set(&campaign.summary["globally_absent_from_entire_cohort_archive"])?,
        "global archive-absence declaration differs from recomputed evidence",
    )?;
    let mut candidates = Vec::new();
    for (pdu, mut entry) in global_candidates {
        entry["also_in_any_cohort_baseline"] = json!(baseline_global.contains(&pdu));
        let records = array(&entry, "observations")?;
        let captures: BTreeSet<_> = records
            .iter()
            .map(|r| text(r, "same_audio_sha256"))
            .collect::<AuditResult<_>>()?;
        let capture_count = captures.len();
        entry["distinct_audio_files"] = json!(capture_count);
        candidates.push(entry);
    }
    Ok(
        json!({"schema":"ogg-candidate-evidence-inventory-v1","status":"complete",
        "scope":"audit of previously exposed frozen cohort and existing replay; not new holdout",
        "reference_summary_sha256":campaign.sha256,"replay_root":root,
        "completed_observations_verified":observations.len(),"observations":observations,
        "native_new_vs_local_archive":local_extra_count,"native_new_vs_local_archive_and_baseline":both_extra_count,
        "global_archive_absent_pdus":candidates.len(),
        "global_archive_absent_bytes":candidate_keys.iter().map(|p|p.len()).sum::<usize>(),
        "baseline_omissions":missing,"candidates":candidates,
        "mission_attribution_verified":false,"false_accept_rate_qualified":false,
        "publication_ready":false,"deployment_ready":false,
        "limitations":["Matching AX25 addresses do not authenticate transmitter identity.",
            "Secondary replay verifies reproducibility, not an independent reception.",
            "Distinct files/observations are not automatically independent stations or transmissions.",
            "Archive absence is limited to the complete frozen cohort snapshot.",
            "Archive and baseline omit original FCS; no independent FCS claim for those inputs."]}),
    )
}

#[cfg(test)]
mod candidate_tests {
    use super::*;
    fn declared() -> Value {
        let mut pdu = Vec::new();
        for (call, ssid) in [(b"CQ    ", 0x60), (b"N0CALL", 0x61)] {
            pdu.extend(call.iter().map(|b| b << 1));
            pdu.push(ssid);
        }
        pdu.extend([0x03, 0xf0, 0x42]);
        json!({"pdus":[hex::encode(&pdu)],"count":1,"bytes":pdu.len()})
    }
    #[test]
    fn declared_set_rejects_duplicates_and_wrong_counters() {
        let value = declared();
        assert_eq!(pdu_set(&value).unwrap().len(), 1);
        let mut duplicate = value.clone();
        duplicate["pdus"]
            .as_array_mut()
            .unwrap()
            .push(value["pdus"][0].clone());
        assert!(pdu_set(&duplicate).is_err());
        for field in ["count", "bytes"] {
            let mut changed = value.clone();
            changed[field] = json!(999);
            assert!(pdu_set(&changed).is_err());
        }
    }
    #[test]
    fn malformed_address_and_hex_cannot_enter_ledger() {
        assert!(address_pair(&[0; 30]).is_err());
        let mut value = declared();
        value["pdus"][0] = json!("not hex");
        assert!(pdu_set(&value).is_err());
    }
}
