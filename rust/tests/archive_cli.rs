//! Native CLI contract checks with an empty PATH; no Python orchestration.
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::process::{Command, Output};
use telemetry_yield_rs::{
    archive::{
        self, Sealed,
        cohort::{Catalogue, Cohort, Exposure, Observation, Protocol},
        runner::{Release, Row},
    },
    input,
};

fn call(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_framelift-campaign"))
        .args(args)
        .env_clear()
        .env("PATH", "")
        .output()
        .unwrap()
}
fn path(p: &Path) -> &str {
    p.to_str().unwrap()
}
fn fixture(dir: &Path) -> (std::path::PathBuf, std::path::PathBuf) {
    let mut protocol: Protocol = serde_json::from_str(include_str!(
        "../../config/archive-qualification-20260916.json"
    ))
    .unwrap();
    protocol.target = 2;
    protocol.per_mission = 1;
    protocol.minimum_missions = 2;
    protocol.minimum_stations = 2;
    protocol.missions.truncate(2);
    let observations = protocol
        .missions
        .iter()
        .enumerate()
        .map(|(i, m)| Observation {
            id: 1_000_001 + i as u64,
            norad: m.norad,
            station: 1 + i as u64,
            start: "2026-07-08T01:00:00Z".into(),
            end: "2026-07-08T01:05:00Z".into(),
            audio_url: "https://network.satnogs.org/fixture.ogg".into(),
            transmitter: "fixture".into(),
            mode: "FSK".into(),
            baud: 9600,
            reported_signal: Some(true),
        })
        .collect();
    let catalogue = dir.join("catalogue.json");
    Sealed::write(
        &catalogue,
        Catalogue {
            schema: "framelift-archive-catalogue-v1".into(),
            protocol_sha256: archive::digest(&protocol).unwrap(),
            protocol,
            pagination_complete: true,
            observations,
            pages: vec![],
            rejected_metadata_rows: 0,
            amendment: None,
        },
    )
    .unwrap();
    let evidence = dir.join("evidence.txt");
    std::fs::write(&evidence, b"fixture exposure evidence").unwrap();
    let exposures = dir.join("exposures.json");
    input::write_json_new(
        &exposures,
        &Exposure {
            inventory_scope: "fixture only".into(),
            evidence: vec![input::identity(&evidence).unwrap()],
            observation_ids: BTreeSet::new(),
            stations: BTreeSet::new(),
            excluded_mission_days: BTreeSet::new(),
            complete_inventory_attested: false,
        },
    )
    .unwrap();
    (catalogue, exposures)
}

#[test]
fn freeze_is_native_sealed_and_never_overwrites() {
    let temp = tempfile::tempdir().unwrap();
    let (catalogue, exposures) = fixture(temp.path());
    let cohort = temp.path().join("cohort.json");
    let args = [
        "freeze",
        "--catalogue",
        path(&catalogue),
        "--exposures",
        path(&exposures),
        "--output",
        path(&cohort),
    ];
    let output = call(&args);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let sealed = Sealed::<Cohort>::read(&cohort).unwrap();
    assert_eq!(sealed.content.observations.len(), 2);
    let original = std::fs::read(&cohort).unwrap();
    assert!(!call(&args).status.success());
    assert_eq!(std::fs::read(&cohort).unwrap(), original);
    let mut modified: Value = serde_json::from_slice(&original).unwrap();
    modified["content"]["observations"][0]["station"] = json!(999);
    std::fs::write(&cohort, serde_json::to_vec(&modified).unwrap()).unwrap();
    assert!(Sealed::<Cohort>::read(&cohort).is_err());
}

#[test]
fn changed_exposure_evidence_prevents_freeze_without_output() {
    let temp = tempfile::tempdir().unwrap();
    let (catalogue, exposures) = fixture(temp.path());
    std::fs::write(temp.path().join("evidence.txt"), b"changed").unwrap();
    let cohort = temp.path().join("cohort.json");
    let output = call(&[
        "freeze",
        "--catalogue",
        path(&catalogue),
        "--exposures",
        path(&exposures),
        "--output",
        path(&cohort),
    ]);
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(!cohort.exists());
}

#[test]
fn invalid_metadata_protocol_fails_before_network_or_directory_creation() {
    let temp = tempfile::tempdir().unwrap();
    let protocol = temp.path().join("protocol.json");
    std::fs::write(&protocol, b"{\"schema\":\"invalid\"}").unwrap();
    let out = temp.path().join("acquisition");
    let result = call(&[
        "metadata",
        "--protocol",
        path(&protocol),
        "--output",
        path(&out),
    ]);
    assert!(!result.status.success());
    assert!(!out.exists());
}

#[test]
fn sampling_amendment_preserves_original_and_cannot_change_receiver_or_salt() {
    let temp = tempfile::tempdir().unwrap();
    let (catalogue, _) = fixture(temp.path());
    let saved = std::fs::read(&catalogue).unwrap();
    let original = Sealed::<Catalogue>::read(&catalogue).unwrap();
    let mut revised = original.content.protocol.clone();
    revised.per_mission = 2;
    let protocol = temp.path().join("revised.json");
    input::write_json_new(&protocol, &revised).unwrap();
    let output = temp.path().join("amended.json");
    let result = call(&[
        "amend",
        "--catalogue",
        path(&catalogue),
        "--protocol",
        path(&protocol),
        "--reason",
        "Metadata-only feasibility adjustment before any waveform download.",
        "--output",
        path(&output),
    ]);
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(std::fs::read(&catalogue).unwrap(), saved);
    let amended = Sealed::<Catalogue>::read(&output).unwrap();
    assert_eq!(
        amended.content.amendment.as_ref().unwrap()["parent_catalogue_sha256"],
        original.sha256
    );
    revised.rank_salt = "changed-ranking-after-inspection".into();
    std::fs::write(&protocol, serde_json::to_vec(&revised).unwrap()).unwrap();
    let forbidden = temp.path().join("forbidden.json");
    assert!(
        !call(&[
            "amend",
            "--catalogue",
            path(&catalogue),
            "--protocol",
            path(&protocol),
            "--reason",
            "Metadata-only feasibility adjustment before any waveform download.",
            "--output",
            path(&forbidden)
        ])
        .status
        .success()
    );
    assert!(!forbidden.exists());
}

#[test]
fn report_retains_missing_and_failed_observations_without_zero_frame_claims() {
    let temp = tempfile::tempdir().unwrap();
    let (catalogue, exposures) = fixture(temp.path());
    let cohort_path = temp.path().join("cohort.json");
    assert!(
        call(&[
            "freeze",
            "--catalogue",
            path(&catalogue),
            "--exposures",
            path(&exposures),
            "--output",
            path(&cohort_path)
        ])
        .status
        .success()
    );
    let cohort = Sealed::<Cohort>::read(&cohort_path).unwrap();
    let release_path = temp.path().join("release.json");
    // This failure-only report never executes these roles, but registration
    // still needs complete, well-formed artifact identities.
    let executable = input::identity(Path::new(env!("CARGO_BIN_EXE_framelift-campaign"))).unwrap();
    let release = Sealed::write(
        &release_path,
        Release {
            schema: "framelift-archive-release-v1".into(),
            cohort_sha256: cohort.sha256,
            frozen_utc: "2026-09-16T00:00:00Z".into(),
            executables: ["receiver", "direwolf", "gr_satellites", "ffmpeg"]
                .map(|role| (role.to_owned(), executable.clone()))
                .into_iter()
                .collect::<BTreeMap<_, _>>(),
            scheduler_policy: telemetry_yield_rs::progressive::scheduler::Policy::MarginalYield
                .identity(),
            dependency_files: vec![executable],
            baseline_timeout_seconds: 120,
            representation: "fixture".into(),
        },
    )
    .unwrap();
    let results = temp.path().join("results");
    std::fs::create_dir(&results).unwrap();
    let id = cohort.content.observations[0].id;
    let rowdir = results.join(id.to_string());
    std::fs::create_dir(&rowdir).unwrap();
    Sealed::write(
        &rowdir.join("row.json"),
        Row {
            id,
            release_sha256: release.sha256,
            source: None,
            pcm: None,
            preparation: None,
            arms: vec![],
            error: Some("download unavailable".into()),
        },
    )
    .unwrap();
    let report = temp.path().join("report.json");
    let output = call(&[
        "report",
        "--cohort",
        path(&cohort_path),
        "--release",
        path(&release_path),
        "--results",
        path(&results),
        "--output",
        path(&report),
    ]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["selected"], 2);
    assert_eq!(value["terminal_observations"], 1);
    assert_eq!(value["not_yet_terminal"], 1);
    assert_eq!(value["aggregates"], json!({}));
    assert_eq!(value["publication_ready"], false);
}
