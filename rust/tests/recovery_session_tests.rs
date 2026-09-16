use super::*;
use std::io::Write;
#[path = "../../examples/support/advanced_iq_fixture.rs"]
mod fixture;

fn setup() -> (tempfile::TempDir, std::path::PathBuf, Plan) {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("input.cf32");
    let c = fixture::config();
    let mut iq = fixture::Noise(99).fill(4096, 0.01);
    for (offset, sequence) in [(128, 3), (2176, 7)] {
        fixture::add(
            &mut iq,
            &fixture::symbols(&c, &fixture::frame(sequence), None),
            offset,
            1.,
            0.,
        );
    }
    let mut file = std::fs::File::create(&path).unwrap();
    for z in iq {
        file.write_all(&(z.re as f32).to_le_bytes()).unwrap();
        file.write_all(&(z.im as f32).to_le_bytes()).unwrap();
    }
    let plan = Plan {
        format: generic::InputFormat::Cf32Le,
        sample_rate_hz: 4000,
        windows: vec![
            Window {
                start_sample: 0,
                sample_count: 2048,
            },
            Window {
                start_sample: 2048,
                sample_count: 2048,
            },
        ],
        receiver: c,
    };
    (temp, path, plan)
}

#[test]
fn resumed_and_uninterrupted_sessions_have_identical_scientific_results() {
    let (temp, path, plan) = setup();
    let partial = temp.path().join("partial");
    let first = run(&path, &plan, &partial, false, 1).unwrap();
    assert_eq!(first["summary"]["status"], "paused");
    assert_eq!(first["summary"]["completed_windows"], 1);
    let resumed = run(&path, &plan, &partial, true, 1).unwrap();
    assert_eq!(resumed["executed_windows"], 1);
    assert_eq!(resumed["reused_windows"], 1);
    assert_eq!(resumed["summary"]["unique_frames"], 2);
    let full = run(&path, &plan, &temp.path().join("full"), false, 2).unwrap();
    assert_eq!(resumed["summary"], full["summary"]);
    let again = run(&path, &plan, &partial, true, 2).unwrap();
    assert_eq!(again["executed_windows"], 0);
    assert_eq!(again["reused_windows"], 2);
    assert_eq!(again["summary"], full["summary"]);
}

#[test]
fn resume_refuses_changed_source_plan_and_corrupt_cached_report() {
    let (temp, path, mut plan) = setup();
    let out = temp.path().join("session");
    run(&path, &plan, &out, false, 1).unwrap();
    plan.receiver.maximum_work += 1;
    assert!(
        run(&path, &plan, &out, true, 1)
            .unwrap_err()
            .contains("identity mismatch")
    );
    plan.receiver.maximum_work -= 1;
    let task = out.join("window-000000.json");
    let mut record = input::read_json(&task).unwrap();
    record["report"]["frames"] = json!([]);
    fs::write(&task, serde_json::to_vec(&record).unwrap()).unwrap();
    assert!(
        run(&path, &plan, &out, true, 1)
            .unwrap_err()
            .contains("integrity check")
    );
    let mut file = OpenOptions::new().append(true).open(&path).unwrap();
    file.write_all(&[0u8; 8]).unwrap();
    assert!(
        run(&path, &plan, &out, true, 1)
            .unwrap_err()
            .contains("identity mismatch")
    );
}

#[test]
fn fresh_output_window_bounds_and_concurrent_lock_fail_closed() {
    let (temp, path, mut plan) = setup();
    let out = temp.path().join("session");
    run(&path, &plan, &out, false, 1).unwrap();
    assert!(run(&path, &plan, &out, false, 1).is_err());
    let _lock = lock(&out).unwrap();
    assert!(
        run(&path, &plan, &out, true, 1)
            .unwrap_err()
            .contains("already running")
    );
    plan.windows[0].start_sample = usize::MAX;
    assert!(run(&path, &plan, &temp.path().join("bad"), false, 1).is_err());
    assert!(!temp.path().join("bad").exists());
}

#[cfg(unix)]
#[test]
fn cached_task_symlink_is_never_trusted() {
    let (temp, path, plan) = setup();
    let out = temp.path().join("session");
    run(&path, &plan, &out, false, 1).unwrap();
    std::os::unix::fs::symlink(&path, out.join("window-000001.json")).unwrap();
    assert!(
        run(&path, &plan, &out, true, 1)
            .unwrap_err()
            .contains("not a regular file")
    );
}
