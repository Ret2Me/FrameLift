use super::*;

fn inputs() -> (Value, Value) {
    let mut observations = Vec::new();
    let mut candidates = Vec::new();
    for i in 1..=38 {
        let id = 20000 + i;
        observations.push(
            json!({"observation_id":id,"satellite_id":format!("sat-{}",i%8),"mode":"FSK",
            "frequency_hz":400000000+i,"start":"2026-09-03T00:00:00Z","end":"2026-09-03T00:00:30Z",
            "frame_count":i,"frames_recovered":true,"frames":[{"secret":i}]}),
        );
        candidates.push(json!({"observation_id":id,"key":format!("observation_{id}.iq"),"size_bytes":4000+i*4,"url":format!("https://invalid/observation_{id}.iq")}));
    }
    (
        json!({"observations":observations}),
        json!({"candidates":candidates}),
    )
}

#[test]
fn python_salt_and_integer_rank_vectors_match_at_byte_boundaries() {
    // Frozen from the original Python functions on 2026-09-15. These values
    // are literals: the Rust test does not execute a second implementation.
    let salt = derive_selection_salt("polyitan-v2", &"1".repeat(64), &"AB".repeat(64)).unwrap();
    assert_eq!(
        hex::encode(salt),
        "10ac8ed87ea9f0fa1bfb13117b58a7c01b8874766977d1458536b095ee87eee7"
    );
    for (id, expected) in [
        (
            1,
            "182cd6c1ba3a50f782ad8fb1185231dd320e1b2b5c2c0aeb1960b93e5277a175",
        ),
        (
            2,
            "62699f52a7129a4cd0cac1be341a825f675d8fedbead0b192fb0ee869fe3ddb6",
        ),
        (
            255,
            "180a7f132cef24304f62200ca54faf82fcf10c4def31a3af263b100f0a0b137a",
        ),
        (
            256,
            "5ef66eb7ae5072c7a6be2e2ffb8904378f3c7ebf58bf66d5e35e917c2d8fae29",
        ),
        (
            65535,
            "e7f42c26f233aa936eaa8b0e01ae838f2806958a499b67392f46e8061148a84f",
        ),
        (
            65536,
            "cd02923ce3c8ea35150d55ad0f394ef5d49102f09c14907d878098b21d2001e7",
        ),
        (
            1 << 63,
            "fa3029014586e5e911343651aada3981a278a7ba06dd7905b3a53c892ac55d75",
        ),
        (
            u64::MAX,
            "e01815398e093775fa7ecfa1b3b3fc35d389db09cb855251b2b6b5edde23dd2e",
        ),
    ] {
        assert_eq!(rank_integer(&salt, id).unwrap(), expected);
    }
    assert!(rank_integer(&salt, 0).is_err());
    assert_ne!(
        salt,
        derive_selection_salt("another", &"1".repeat(64), &"AB".repeat(64)).unwrap()
    );
    for (domain, sha, output) in [
        ("", "1".repeat(64), "AB".repeat(64)),
        ("x\ny", "1".repeat(64), "AB".repeat(64)),
        ("x", "A".repeat(64), "AB".repeat(64)),
        ("x", "1".repeat(64), "AB".repeat(63)),
    ] {
        assert!(derive_selection_salt(domain, &sha, &output).is_err());
    }
}

#[test]
fn projected_cohort_matches_python_and_ignores_outcomes_and_input_order() {
    let (mut catalogue, mut plan) = inputs();
    let modes = vec!["FSK".into()];
    let pool = build_eligible_pool(&catalogue, &plan, &modes, &[20001]).unwrap();
    assert_eq!(pool.len(), 37);
    assert!(
        pool.iter()
            .all(|row| row.get("frames").is_none() && row.get("frame_count").is_none())
    );
    let selected = select_public_random_cohort(&pool, &[b'a'; 32], 30, 6, 3).unwrap();
    let ids: Vec<_> = selected
        .iter()
        .map(|v| v["observation_id"].as_u64().unwrap())
        .collect();
    assert_eq!(
        ids,
        vec![
            20015, 20017, 20013, 20007, 20037, 20018, 20036, 20038, 20031, 20011, 20003, 20024,
            20020, 20022, 20021, 20035, 20008, 20030, 20033, 20014, 20029, 20009, 20028, 20025,
            20004, 20012, 20032, 20026, 20005, 20023
        ]
    );
    for row in catalogue["observations"].as_array_mut().unwrap() {
        row["frames"] = json!([{"secret":"changed"}]);
        row["frame_count"] = json!(999999);
        row["frames_recovered"] = json!(false);
    }
    catalogue["observations"].as_array_mut().unwrap().reverse();
    plan["candidates"].as_array_mut().unwrap().reverse();
    assert_eq!(
        pool,
        build_eligible_pool(&catalogue, &plan, &modes, &[20001]).unwrap()
    );
    let reversed: Vec<_> = pool.into_iter().rev().collect();
    assert_eq!(
        selected,
        select_public_random_cohort(&reversed, &[b'a'; 32], 30, 6, 3).unwrap()
    );
}

#[test]
fn malformed_pool_and_insufficient_diversity_fail_closed() {
    let (catalogue, mut plan) = inputs();
    let modes = vec!["FSK".into()];
    plan["candidates"][0]["size_bytes"] = json!(3);
    assert!(
        build_eligible_pool(&catalogue, &plan, &modes, &[])
            .unwrap_err()
            .contains("multiple of four")
    );
    let (_, mut plan) = inputs();
    plan["candidates"][0]["key"] = json!("observation_020001.iq");
    assert!(build_eligible_pool(&catalogue, &plan, &modes, &[]).is_err());
    let (_, mut plan) = inputs();
    let duplicate = plan["candidates"][0].clone();
    plan["candidates"].as_array_mut().unwrap().push(duplicate);
    assert!(build_eligible_pool(&catalogue, &plan, &modes, &[]).is_err());
    let (_, plan) = inputs();
    let pool = build_eligible_pool(&catalogue, &plan, &modes, &[]).unwrap();
    assert!(
        select_public_random_cohort(&pool, &[0; 32], 30, 1, 3)
            .unwrap_err()
            .contains("insufficient")
    );
    let mut one = pool.clone();
    for row in &mut one {
        row["satellite_id"] = json!("one");
    }
    assert!(
        select_public_random_cohort(&one, &[0; 32], 3, 3, 2)
            .unwrap_err()
            .contains("too few")
    );
    assert!(select_public_random_cohort(&pool, &[0; 32], 0, 1, 1).is_err());
    let mut duplicate = pool.clone();
    duplicate.push(pool[0].clone());
    assert!(
        select_public_random_cohort(&duplicate, &[0; 32], 3, 3, 2)
            .unwrap_err()
            .contains("duplicate")
    );
}

fn pulse() -> Value {
    json!({"pulse":{"version":"2.0","period":60000,"statusCode":0,"timeStamp":"2026-09-03T12:45:00.000Z",
        "outputValue":"AB".repeat(64),"certificateId":hex::encode(Sha512::digest([1,2,3])),"signatureValue":"12".repeat(64),"chainIndex":2,"pulseIndex":123,
        "uri":"https://beacon.nist.gov/beacon/2.0/chain/2/pulse/123"}})
}
const PEM: &str = "-----BEGIN CERTIFICATE-----\nAQID\n-----END CERTIFICATE-----\n";

#[test]
fn pulse_binding_does_not_claim_certificate_or_signature_authentication() {
    // Deliberately not an X.509 certificate: this gate binds opaque DER bytes,
    // just as the reference did. It does not parse/verify certificate trust.
    let result = validate_nist_beacon_pulse(&pulse(), "2026-09-03T12:45:00Z", PEM).unwrap();
    assert_eq!(result["certificate_identifier_verified"], true);
    assert_eq!(result["pulse_signature_verified"], false);
    assert_eq!(result["time_stamp"], "2026-09-03T12:45:00.000Z");
    // Complete frozen Python result, not just its two trust flags.
    let certificate_hash = "27864cc5219a951a7a6e52b8c8dddf6981d098da1658d96258c870b2c88dfbcb51841aea172a28bafa6a79731165584677066045c959ed0f9929688d04defc29";
    assert_eq!(
        result,
        json!({
            "version":"2.0","time_stamp":"2026-09-03T12:45:00.000Z",
            "period_milliseconds":60000,"chain_index":2,"pulse_index":123,
            "uri":"https://beacon.nist.gov/beacon/2.0/chain/2/pulse/123",
            "output_value":"AB".repeat(64),
            "output_value_sha256":"ec65c8798ecf95902413c40f7b9e6d4b0068885f5f324aba1f9ba1c8e14aea61",
            "certificate_id":certificate_hash,"certificate_der_sha512":certificate_hash,
            "signature_value_sha256":"a146d2d7abb39cab72234d920574b940835a4ee3dbcfd5c5c7ae4ba5d21d4802",
            "certificate_identifier_verified":true,"pulse_signature_verified":false
        })
    );
    assert!(validate_nist_beacon_pulse(&pulse(), "2026-09-03T12:46:00Z", PEM).is_err());
    assert!(validate_nist_beacon_pulse(&pulse(), "2026-09-03T12:45:00", PEM).is_err());
    assert!(validate_nist_beacon_pulse(&pulse(), "2026-09-03T12:45:00Z", "bad PEM").is_err());
    for (key, bad) in [
        ("certificateId", json!("0".repeat(128))),
        ("outputValue", json!("bad")),
        ("signatureValue", json!("f")),
        ("statusCode", json!(1)),
        ("statusCode", json!(false)),
        ("period", json!(true)),
        ("chainIndex", json!(0)),
        ("pulseIndex", json!(true)),
    ] {
        let mut doc = pulse();
        doc["pulse"][key] = bad;
        assert!(
            validate_nist_beacon_pulse(&doc, "2026-09-03T12:45:00Z", PEM).is_err(),
            "{key}"
        );
    }
}
