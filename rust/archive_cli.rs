//! Native, inspectable archival research commands.
use clap::{Parser, Subcommand};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::ExitCode;
use telemetry_yield_rs::archive::cohort;
use telemetry_yield_rs::archive::report;
use telemetry_yield_rs::archive::runner;

#[derive(Parser)]
#[command(
    name = "framelift-campaign",
    about = "Native archival cohort, acquisition and paired receiver experiments"
)]
struct Args {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Declare a metadata-feasibility sampling amendment before outcome access.
    Amend {
        #[arg(long)]
        catalogue: PathBuf,
        #[arg(long)]
        protocol: PathBuf,
        #[arg(long)]
        reason: String,
        #[arg(long)]
        output: PathBuf,
    },
    /// Audit prior local exposure without reading waveforms or credentials.
    Inventory {
        #[arg(long)]
        work_root: PathBuf,
        #[arg(long)]
        prior_manifest: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Fetch bounded metadata only; never selects by a decoder outcome.
    Metadata {
        #[arg(long)]
        protocol: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Freeze a diversity-capped cohort before fetching waveforms.
    Freeze {
        #[arg(long)]
        catalogue: PathBuf,
        #[arg(long)]
        exposures: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Download every selected recording; retain failures without replacement.
    Acquire {
        #[arg(long)]
        cohort: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Bind executable/dependency identities to an already frozen cohort.
    Register {
        #[arg(long)]
        cohort: PathBuf,
        #[arg(long)]
        receiver: PathBuf,
        #[arg(long)]
        direwolf: PathBuf,
        #[arg(long)]
        gr_satellites: PathBuf,
        #[arg(long)]
        ffmpeg: PathBuf,
        #[arg(long)]
        dependency: Vec<PathBuf>,
        #[arg(long)]
        output: PathBuf,
    },
    /// Run the frozen paired experiment; external baselines stay separate.
    Run {
        #[arg(long)]
        cohort: PathBuf,
        #[arg(long)]
        release: PathBuf,
        #[arg(long)]
        acquisition: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        resume: bool,
    },
    /// Re-audit retained packets and report paired gains, losses and attrition.
    Report {
        #[arg(long)]
        cohort: PathBuf,
        #[arg(long)]
        release: PathBuf,
        #[arg(long)]
        results: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
}

fn execute(command: Command) -> Result<Value, String> {
    match command {
        Command::Amend {
            catalogue,
            protocol,
            reason,
            output,
        } => cohort::amend(&catalogue, &protocol, &reason, &output),
        Command::Inventory {
            work_root,
            prior_manifest,
            output,
        } => telemetry_yield_rs::archive::exposure::inventory(&work_root, &prior_manifest, &output),
        Command::Metadata { protocol, output } => cohort::acquire_metadata(&protocol, &output),
        Command::Freeze {
            catalogue,
            exposures,
            output,
        } => cohort::freeze(&catalogue, &exposures, &output),
        Command::Acquire { cohort, output } => cohort::acquire_waveforms(&cohort, &output),
        Command::Register {
            cohort,
            receiver,
            direwolf,
            gr_satellites,
            ffmpeg,
            dependency,
            output,
        } => runner::register(
            &cohort,
            &BTreeMap::from([
                ("receiver".into(), receiver),
                ("direwolf".into(), direwolf),
                ("gr_satellites".into(), gr_satellites),
                ("ffmpeg".into(), ffmpeg),
            ]),
            &dependency,
            &output,
        ),
        Command::Run {
            cohort,
            release,
            acquisition,
            output,
            resume,
        } => runner::run(&cohort, &release, &acquisition, &output, resume),
        Command::Report {
            cohort,
            release,
            results,
            output,
        } => report::create(&cohort, &release, &results, &output),
    }
}

fn main() -> ExitCode {
    match execute(Args::parse().command) {
        Ok(value) => match serde_json::to_writer_pretty(std::io::stdout().lock(), &value) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("output: {error}");
                ExitCode::FAILURE
            }
        },
        Err(error) => {
            eprintln!("framelift-campaign: {error}");
            ExitCode::FAILURE
        }
    }
}
