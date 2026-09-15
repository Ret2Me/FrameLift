//! Reproducible developer checks. No shell interpolation or runtime dependencies.

use std::ffi::OsString;
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const USAGE: &str = "cargo xtask <check|fmt|lint|test|cuda-check|research-check>

  check          Format, strict CPU lint, CPU tests, strict docs, CUDA host checks
  fmt            Format shipping Rust sources (research examples stay unchanged)
  lint           Strict CPU library/binary/tool lint
  test           Library, binary, CLI, independent auditor and tool tests (CPU)
  cuda-check     Strict CUDA lint and host tests; does NOT qualify a GPU
  research-check Compile all retained research targets; warnings remain visible";

const FORMAT_ROOTS: &[&str] = &[
    "rust/lib.rs",
    "rust/main.rs",
    "rust/sstv_cli.rs",
    "rust/meteor_cli.rs",
    "rust/tests/cli_integration.rs",
    "tools/xtask/src/main.rs",
];

#[derive(Debug, PartialEq)]
struct Step {
    tool: &'static str,
    args: Vec<&'static str>,
    env: Vec<(&'static str, &'static str)>,
}

fn cargo(args: &[&'static str]) -> Step {
    let mut locked = vec![args[0], "--locked"];
    locked.extend_from_slice(&args[1..]);
    Step {
        tool: "cargo",
        args: locked,
        env: vec![],
    }
}

fn format(check: bool) -> Step {
    let mut args = vec!["--edition", "2024"];
    if check {
        args.push("--check");
    }
    args.extend_from_slice(FORMAT_ROOTS);
    Step {
        tool: "rustfmt",
        args,
        env: vec![],
    }
}

fn plan(task: &str) -> Result<Vec<Step>, String> {
    let steps = match task {
        "fmt" => vec![format(false)],
        "lint" => vec![
            cargo(&[
                "clippy",
                "-p",
                "telemetry-yield-rs",
                "--no-default-features",
                "--lib",
                "--bins",
                "--",
                "-D",
                "warnings",
            ]),
            cargo(&[
                "clippy",
                "-p",
                "xtask",
                "--all-targets",
                "--",
                "-D",
                "warnings",
            ]),
        ],
        "test" => vec![
            cargo(&[
                "test",
                "-p",
                "telemetry-yield-rs",
                "--no-default-features",
                "--lib",
                "--bins",
                "--test",
                "cli_integration",
                "--example",
                "decoder_readiness_audit",
            ]),
            cargo(&["test", "-p", "xtask"]),
        ],
        "cuda-check" => vec![
            cargo(&[
                "clippy",
                "-p",
                "telemetry-yield-rs",
                "--features",
                "cuda",
                "--lib",
                "--bins",
                "--",
                "-D",
                "warnings",
            ]),
            cargo(&[
                "test",
                "-p",
                "telemetry-yield-rs",
                "--features",
                "cuda",
                "--lib",
                "compute",
            ]),
        ],
        "research-check" => vec![cargo(&[
            "check",
            "-p",
            "telemetry-yield-rs",
            "--all-targets",
            "--features",
            "cuda",
        ])],
        "check" => {
            let mut steps = vec![format(true)];
            steps.extend(plan("lint")?);
            steps.extend(plan("test")?);
            let mut docs = cargo(&[
                "doc",
                "-p",
                "telemetry-yield-rs",
                "--no-default-features",
                "--no-deps",
                "--lib",
            ]);
            docs.env.push(("RUSTDOCFLAGS", "-D warnings"));
            steps.push(docs);
            steps.extend(plan("cuda-check")?);
            steps
        }
        _ => return Err(format!("unknown task {task:?}\n{USAGE}")),
    };
    Ok(steps)
}

fn parse_task(args: &[String]) -> Result<Option<&str>, String> {
    match args {
        [] => Ok(None),
        [task] if ["help", "--help", "-h"].contains(&task.as_str()) => Ok(None),
        [task] => {
            plan(task)?;
            Ok(Some(task))
        }
        _ => Err(format!("expected exactly one task\n{USAGE}")),
    }
}

fn workspace() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("xtask lives at tools/xtask")
        .to_path_buf()
}

fn run_step(root: &Path, step: &Step) -> Result<(), String> {
    let tool = if step.tool == "cargo" {
        std::env::var_os("CARGO").unwrap_or_else(|| OsString::from("cargo"))
    } else {
        // `cargo` may be invoked by absolute path, without rustup shims on PATH.
        // Prefer rustfmt from that same selected toolchain when available.
        std::env::var_os("CARGO")
            .and_then(|cargo| Path::new(&cargo).parent().map(|dir| dir.join(step.tool)))
            .filter(|path| path.is_file())
            .map(PathBuf::into_os_string)
            .unwrap_or_else(|| OsString::from(step.tool))
    };
    eprintln!("\n> {} {}", step.tool, step.args.join(" "));
    let status = Command::new(tool)
        .current_dir(root)
        .args(&step.args)
        .envs(step.env.iter().copied())
        .status()
        .map_err(|error| format!("cannot start {}: {error}", step.tool))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "{} exited with {status}; remaining checks were not run",
            step.tool
        ))
    }
}

fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let Some(task) = parse_task(&args)? else {
        println!("{USAGE}");
        return Ok(());
    };
    let root = workspace();
    // Building while a hash-sensitive receiver test is running can replace its
    // executable. Serialize cooperating quality runs across feature variants.
    // Direct Cargo invocations must follow the same no-concurrent-build rule.
    fs::create_dir_all(root.join("target")).map_err(|error| error.to_string())?;
    let lock = File::options()
        .create(true)
        .read(true)
        .write(true)
        .truncate(false)
        .open(root.join("target/quality.lock"))
        .map_err(|error| error.to_string())?;
    lock.try_lock()
        .map_err(|error| format!("another quality run holds the workspace lock: {error}"))?;
    for step in plan(task)? {
        run_step(&root, &step)?;
    }
    eprintln!("\n{task}: passed (GPU execution and release qualification are separate gates)");
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_tasks_and_extra_arguments_fail() {
        assert!(parse_task(&["deploy".into()]).is_err());
        assert!(parse_task(&["check".into(), "--ignored".into()]).is_err());
        assert_eq!(parse_task(&[]).unwrap(), None);
        assert_eq!(parse_task(&["check".into()]).unwrap(), Some("check"));
    }

    #[test]
    fn every_cargo_step_uses_the_lockfile() {
        for task in [
            "check",
            "fmt",
            "lint",
            "test",
            "cuda-check",
            "research-check",
        ] {
            for step in plan(task).unwrap() {
                assert!(step.tool != "cargo" || step.args.contains(&"--locked"));
            }
        }
    }

    #[test]
    fn check_formats_without_modifying_and_keeps_research_separate() {
        let steps = plan("check").unwrap();
        assert_eq!(steps[0], format(true));
        assert!(
            !FORMAT_ROOTS
                .iter()
                .any(|path| path.starts_with("examples/"))
        );
        assert!(
            steps
                .iter()
                .any(|s| s.env.contains(&("RUSTDOCFLAGS", "-D warnings")))
        );
    }

    #[test]
    fn cpu_tests_finish_before_cuda_builds_start() {
        let steps = plan("check").unwrap();
        let cpu = steps
            .iter()
            .position(|s| s.args.contains(&"cli_integration"))
            .unwrap();
        let cuda = steps.iter().position(|s| s.args.contains(&"cuda")).unwrap();
        assert!(cpu < cuda);
    }

    #[test]
    fn host_cuda_check_never_claims_an_ignored_hardware_test() {
        for step in plan("cuda-check").unwrap() {
            assert!(!step.args.contains(&"--ignored"));
        }
    }

    #[test]
    fn failed_child_is_reported() {
        let step = Step {
            tool: "telemetry-yield-nonexistent-test-tool",
            args: vec![],
            env: vec![],
        };
        assert!(
            run_step(&workspace(), &step)
                .unwrap_err()
                .contains("cannot start")
        );
    }
}
